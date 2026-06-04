"""Semantic query cache with LRU + TTL eviction and cosine similarity matching."""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.config import CacheConfig
from app.core.interfaces import Embedder, SemanticCache

logger = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    query_embedding: np.ndarray
    answer: str
    sources: list[dict[str, Any]]
    hit_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    ttl_seconds: float = 3600.0

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds

    def touch(self) -> None:
        self.hit_count += 1
        self.last_accessed = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "hit_count": self.hit_count,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "ttl_seconds": self.ttl_seconds,
        }


class DefaultSemanticCache(SemanticCache):
    """In-memory semantic cache with optional disk persistence."""

    def __init__(self, config: CacheConfig, embedder: Embedder | None = None):
        self.config = config
        self._embedder = embedder
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._persist_path = Path(config.persist_path)
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_save = 0.0
        self._stop_cleanup = False
        self._stats = {
            "total_hits": 0,
            "total_misses": 0,
            "total_evictions": 0,
            "total_expires": 0,
        }
        self._load_from_disk()
        self._start_cleanup_thread()
        logger.info(
            "SemanticCache initialized: max_size=%d, threshold=%.2f",
            config.max_size, config.similarity_threshold,
        )

    # ---------- Public API ----------

    def get(
        self, query: str, query_embedding: list[float] | None = None
    ) -> tuple[tuple[str, list[dict[str, Any]]] | None, list[float] | None]:
        """Returns ((answer, sources), embedding) on hit, (None, embedding) on miss.
        
        The query embedding is always returned (if computable) so callers can
        reuse it for put() to avoid redundant embedding computation.
        """
        if not self.config.enabled:
            return (None, None)

        # Compute embedding OUTSIDE the lock to avoid blocking other cache ops
        emb = query_embedding
        if emb is None and self._embedder is not None:
            emb = self._embedder.embed_query(query)
        if emb is None:
            with self._lock:
                self._stats["total_misses"] += 1
            return (None, None)
        query_vec = np.array(emb, dtype=np.float32)

        with self._lock:
            self._evict_expired()
            if not self._cache:
                self._stats["total_misses"] += 1
                return (None, emb)

            match = self._find_best_match(query_vec)
            if match is None:
                self._stats["total_misses"] += 1
                return (None, emb)

            key, entry = match
            entry.touch()
            self._cache.move_to_end(key)
            self._stats["total_hits"] += 1
            logger.debug("Cache HIT: query='%s...' hits=%d", query[:30], entry.hit_count)
            return ((entry.answer, entry.sources), emb)

    def put(
        self,
        query: str,
        answer: str,
        sources: list[dict[str, Any]],
        query_embedding: list[float] | None = None,
        ttl: int | None = None,
    ) -> None:
        if not self.config.enabled:
            return

        # Compute embedding OUTSIDE the lock
        emb = query_embedding
        if emb is None and self._embedder is not None:
            emb = self._embedder.embed_query(query)
        if emb is None:
            return
        query_vec = np.array(emb, dtype=np.float32)
        key = self._compute_key(query_vec)

        entry = _CacheEntry(
            query_embedding=query_vec,
            answer=answer,
            sources=sources,
            ttl_seconds=ttl or self._select_ttl(query, answer),
        )
        with self._lock:
            while len(self._cache) >= self.config.max_size:
                self._evict_lru()
            self._cache[key] = entry
            self._maybe_persist()

    def invalidate(self, pattern: str | None = None) -> int:
        with self._lock:
            if pattern is None:
                count = len(self._cache)
                self._cache.clear()
                logger.info("Cache fully invalidated: %d entries", count)
                return count
            to_remove = [k for k, v in self._cache.items() if pattern in v.answer]
            for k in to_remove:
                del self._cache[k]
            logger.info("Cache invalidated: pattern='%s' removed=%d", pattern, len(to_remove))
            return len(to_remove)

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._stats["total_hits"] + self._stats["total_misses"]
            hit_rate = self._stats["total_hits"] / total if total > 0 else 0.0
            return {
                "total_hits": self._stats["total_hits"],
                "total_misses": self._stats["total_misses"],
                "total_evictions": self._stats["total_evictions"],
                "total_expires": self._stats["total_expires"],
                "current_size": len(self._cache),
                "max_size": self.config.max_size,
                "hit_rate": round(hit_rate, 4),
                "total_queries": total,
                "config": {
                    "enabled": self.config.enabled,
                    "similarity_threshold": self.config.similarity_threshold,
                    "max_size": self.config.max_size,
                },
            }

    def stop(self) -> None:
        self._stop_cleanup = True
        self._save_to_disk()
        logger.info("SemanticCache stopped")

    # ---------- Private ----------

    def _find_best_match(
        self, query_vec: np.ndarray
    ) -> tuple[str, _CacheEntry] | None:
        threshold = self.config.similarity_threshold
        best_sim = -1.0
        best = None
        for key, entry in self._cache.items():
            sim = self._cosine_sim(query_vec, entry.query_embedding)
            if sim > best_sim:
                best_sim = sim
                best = (key, entry)
        if best and best_sim >= threshold:
            return best
        return None

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return 0.0
        return float(np.dot(a, b) / norm)

    @staticmethod
    def _compute_key(query_vec: np.ndarray) -> str:
        quantized = np.round(query_vec, 4).tobytes()
        return hashlib.sha256(quantized).hexdigest()[:32]

    def _select_ttl(self, query: str, answer: str) -> float:
        q = query.lower()
        if any(w in q for w in ["副作用", "症状", "不良反应", "疼", "吐", "发烧"]):
            return float(self.config.default_ttl)
        if any(w in q for w in ["指南", "推荐", "方案", "治疗", "一线", "二线"]):
            return float(self.config.guideline_ttl)
        if any(w in q for w in ["是什么", "什么叫", "定义", "介绍"]):
            return float(self.config.medical_fact_ttl)
        return float(self.config.default_ttl)

    def _evict_expired(self) -> None:
        expired = [k for k, v in self._cache.items() if v.is_expired]
        for k in expired:
            del self._cache[k]
            self._stats["total_expires"] += 1

    def _evict_lru(self) -> None:
        if self._cache:
            key, _ = self._cache.popitem(last=False)
            self._stats["total_evictions"] += 1
            logger.debug("LRU evicted: %s...", key[:16])

    def _start_cleanup_thread(self) -> None:
        def loop() -> None:
            while not self._stop_cleanup:
                time.sleep(60)
                if self._stop_cleanup:
                    break
                with self._lock:
                    before = len(self._cache)
                    self._evict_expired()
                    after = len(self._cache)
                if before != after:
                    logger.info("Cache cleanup: %d -> %d", before, after)
                self._maybe_persist()

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        logger.info("Cache cleanup thread started")

    def _maybe_persist(self) -> None:
        now = time.time()
        if now - self._last_save >= self.config.save_interval:
            self._save_to_disk()
            self._last_save = now

    def _save_to_disk(self) -> None:
        try:
            data = {"stats": self._stats, "entries": []}
            for key, entry in self._cache.items():
                item = entry.to_dict()
                item["key"] = key
                item["query_embedding"] = entry.query_embedding.tolist()
                data["entries"].append(item)
            tmp = self._persist_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._persist_path)
        except Exception as e:
            logger.warning("Cache persist failed: %s", e)

    def _load_from_disk(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("entries", []):
                key = item["key"]
                entry = _CacheEntry(
                    query_embedding=np.array(item["query_embedding"], dtype=np.float32),
                    answer=item["answer"],
                    sources=item.get("sources", []),
                    hit_count=item.get("hit_count", 0),
                    created_at=item.get("created_at", time.time()),
                    last_accessed=item.get("last_accessed", time.time()),
                    ttl_seconds=item.get("ttl_seconds", self.config.default_ttl),
                )
                self._cache[key] = entry
            self._stats.update(data.get("stats", {}))
            logger.info("Cache loaded from disk: %d entries", len(self._cache))
        except Exception as e:
            logger.warning("Cache load failed: %s", e)

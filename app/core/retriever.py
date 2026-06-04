"""Hybrid retriever: BM25 + vector search with RRF fusion."""
import logging
import pickle
import threading
from pathlib import Path
from typing import Tuple

import jieba
import numpy as np
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from app.config import CancerBoostingConfig, HybridSearchConfig, RetrievalConfig
from app.core.cancer_booster import CancerTypeBooster
from app.core.interfaces import Embedder, QueryExpander, Retriever, VectorStore

logger = logging.getLogger(__name__)

# Pre-load medical terms into jieba
MEDICAL_TERMS = [
    "非小细胞肺癌", "小细胞肺癌", "免疫检查点抑制剂", "无进展生存期",
    "总生存期", "客观缓解率", "疾病控制率", "病理完全缓解",
    "临床试验", "一线治疗", "二线治疗", "新辅助治疗", "辅助治疗",
    "靶向治疗", "免疫治疗", "化学治疗", "放射治疗",
    # Cancer type terms for BM25 keyword matching
    "乳腺癌", "结直肠癌", "胰腺癌", "甲状腺癌", "肝癌",
    "胃癌", "食管癌", "肾癌", "前列腺癌", "卵巢癌",
    "宫颈癌", "淋巴瘤", "黑色素瘤", "胆管癌", "胆囊癌",
    "鼻咽癌", "喉癌", "口腔癌", "肝细胞癌", "肺腺癌",
    "肺鳞癌", "三阴性乳腺癌", "霍奇金淋巴瘤", "非霍奇金淋巴瘤",
    "软组织肉瘤", "骨肉瘤", "骨髓增殖性肿瘤",
]
for term in MEDICAL_TERMS:
    jieba.add_word(term, freq=1000)


def _tokenize(text: str) -> list[str]:
    return list(jieba.cut_for_search(text.lower()))


def _rrf_fusion(
    bm25_results: list[Tuple[Document, float]],
    vector_results: list[Tuple[Document, float]],
    rrf_k: int = 60,
    bm25_weight: float = 0.4,
    vector_weight: float = 0.6,
) -> list[Document]:
    """Reciprocal Rank Fusion."""
    doc_scores: dict[str, float] = {}
    doc_map: dict[str, Document] = {}

    def _doc_id(doc: Document) -> str:
        return doc.metadata.get("chunk_id", f"{doc.metadata.get('doc_id', '')}_{doc.metadata.get('chunk_index', 0)}")

    for rank, (doc, _) in enumerate(bm25_results):
        did = _doc_id(doc)
        doc_map[did] = doc
        doc_scores[did] = doc_scores.get(did, 0.0) + bm25_weight / (rrf_k + rank + 1)

    for rank, (doc, _) in enumerate(vector_results):
        did = _doc_id(doc)
        doc_map[did] = doc
        doc_scores[did] = doc_scores.get(did, 0.0) + vector_weight / (rrf_k + rank + 1)

    sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for did, rrf_score in sorted_docs:
        doc = doc_map[did]
        doc.metadata["rrf_score"] = rrf_score
        results.append(doc)
    return results


class HybridRetriever(Retriever):
    """BM25 + Vector + RRF hybrid retriever."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        retrieval_config: RetrievalConfig,
        hybrid_config: HybridSearchConfig,
        booster_config: CancerBoostingConfig | None = None,
        expander: QueryExpander | None = None,
    ):
        self.vector_store = vector_store
        self.embedder = embedder
        self.retrieval_config = retrieval_config
        self.hybrid_config = hybrid_config
        self.expander = expander
        self._bm25: BM25Okapi | None = None
        self._bm25_corpus: list[list[str]] = []
        self._bm25_docs: list[Document] = []
        self._bm25_index_path = Path("data/bm25_index.pkl")
        self._bm25_dirty = False
        self._lock = threading.RLock()
        self._load_bm25_index()
        self.booster = CancerTypeBooster(booster_config) if booster_config else None
        if self.booster:
            logger.info("HybridRetriever: CancerTypeBooster ENABLED, multiplier=%.2f", booster_config.boost_multiplier)
        else:
            logger.warning("HybridRetriever: CancerTypeBooster DISABLED")
    def _save_bm25_index(self) -> None:
        try:
            self._bm25_index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._bm25_index_path, "wb") as f:
                pickle.dump(
                    {
                        "corpus": self._bm25_corpus,
                        "doc_contents": [d.page_content for d in self._bm25_docs],
                        "doc_metadatas": [d.metadata for d in self._bm25_docs],
                    },
                    f,
                )
        except Exception as e:
            logger.warning("Failed to save BM25 index: %s", e)

    def _load_bm25_index(self) -> None:
        if not self._bm25_index_path.exists():
            return
        try:
            with open(self._bm25_index_path, "rb") as f:
                data = pickle.load(f)
            self._bm25_corpus = data["corpus"]
            self._bm25_docs = [
                Document(page_content=c, metadata=m)
                for c, m in zip(data["doc_contents"], data["doc_metadatas"])
            ]
            self._bm25 = BM25Okapi(self._bm25_corpus)
            logger.info("BM25 index loaded: %d documents", len(self._bm25_docs))
        except Exception as e:
            logger.warning("Failed to load BM25 index: %s", e)
            self._bm25 = None

    def add_documents(self, documents: list[Document]) -> None:
        if not self.hybrid_config.enabled:
            return
        with self._lock:
            for doc in documents:
                tokens = _tokenize(doc.page_content)
                self._bm25_corpus.append(tokens)
                self._bm25_docs.append(doc)
            if self._bm25_corpus:
                self._bm25_dirty = True

    def delete_documents(self, doc_id: str) -> None:
        """Remove all chunks belonging to a doc_id from BM25 index."""
        if not self.hybrid_config.enabled:
            return
        with self._lock:
            indices_to_remove = [
                i for i, doc in enumerate(self._bm25_docs)
                if doc.metadata.get("doc_id") == doc_id
            ]
            if not indices_to_remove:
                return
            for i in reversed(indices_to_remove):
                self._bm25_corpus.pop(i)
                self._bm25_docs.pop(i)
            self._bm25_dirty = True
        logger.info("BM25: removed %d chunks for doc %s", len(indices_to_remove), doc_id)

    def remove_chunk(self, chunk_id: str) -> bool:
        """Remove a single chunk from BM25 index by chunk_id."""
        if not self.hybrid_config.enabled:
            return False
        with self._lock:
            for i, doc in enumerate(self._bm25_docs):
                if doc.metadata.get("chunk_id") == chunk_id:
                    self._bm25_corpus.pop(i)
                    self._bm25_docs.pop(i)
                    self._bm25_dirty = True
                    return True
            return False

    def update_chunk(self, chunk_id: str, new_text: str) -> bool:
        """Update a single chunk's text in BM25 index."""
        if not self.hybrid_config.enabled:
            return False
        with self._lock:
            for i, doc in enumerate(self._bm25_docs):
                if doc.metadata.get("chunk_id") == chunk_id:
                    self._bm25_corpus[i] = _tokenize(new_text)
                    self._bm25_docs[i] = Document(page_content=new_text, metadata=doc.metadata)
                    self._bm25_dirty = True
                    return True
            return False

    def build_bm25_index(self, documents: list[Document]) -> None:
        if not self.hybrid_config.enabled:
            return
        logger.info("Building BM25 index from %d documents...", len(documents))
        with self._lock:
            self._bm25_docs = documents
            self._bm25_corpus = [_tokenize(d.page_content) for d in documents]
            self._bm25 = BM25Okapi(self._bm25_corpus)
            self._bm25_dirty = False
        self._save_bm25_index()
        logger.info("BM25 index built.")

    def _ensure_bm25(self) -> None:
        """Rebuild BM25 index if dirty."""
        with self._lock:
            if not self._bm25_dirty:
                return
            if self._bm25_corpus:
                self._bm25 = BM25Okapi(self._bm25_corpus)
            else:
                self._bm25 = None
            self._bm25_dirty = False
        self._save_bm25_index()

    def _bm25_search(self, query: str, k: int) -> list[Tuple[Document, float]]:
        self._ensure_bm25()
        with self._lock:
            if self._bm25 is None or not self._bm25_corpus:
                return []
            tokens = _tokenize(query)
            scores = self._bm25.get_scores(tokens)
            indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            return [(self._bm25_docs[i], float(s)) for i, s in indexed[:k]]

    def _vector_search(self, query: str, k: int) -> list[Tuple[Document, float]]:
        query_vec = self.embedder.embed_query(query)
        docs = self.vector_store.search(query_vec, k=k)
        return [(d, d.metadata.get("score", 0.0)) for d in docs]

    def retrieve(self, query: str) -> list[Document]:
        # --- 癌种预检索增强：在 embedding 前注入癌种关键词 ---
        augmented_query = self.booster.augment_query(query) if self.booster else query
        # Detect cancer types for metadata-aware boosting
        detected_types = self.booster.detect_cancer_types(query) if self.booster else []

        expanded = self.expander.expand(augmented_query) if self.expander else augmented_query
        # Increase candidate pool when cancer type is detected (to include minority docs)
        base_k = self.retrieval_config.search_k
        search_k = base_k * 4 if detected_types else base_k

        if self.hybrid_config.enabled and self._bm25 is not None:
            # When cancer type detected, use BM25-only (keyword matching beats vector for specificity)
            if detected_types:
                docs_raw = self._bm25_search(expanded, k=search_k)
                docs = []
                max_bm25 = max(s for _, s in docs_raw) if docs_raw else 1.0
                for d, score in docs_raw:
                    # Normalize BM25 score to [0,1] and store in metadata
                    d.metadata["score"] = min(score / max_bm25, 1.0) if max_bm25 > 0 else 0.0
                    d.metadata["bm25_score"] = score
                    docs.append(d)
                logger.info("CancerBooster: BM25-only mode, %d candidates", len(docs))
            else:
                bm25_results = self._bm25_search(expanded, k=search_k * 2)
                vector_results = self._vector_search(expanded, k=search_k * 2)
                docs = _rrf_fusion(
                    bm25_results,
                    vector_results,
                    rrf_k=self.hybrid_config.rrf_k,
                    bm25_weight=self.hybrid_config.bm25_weight,
                    vector_weight=self.hybrid_config.vector_weight,
                )
                logger.debug("Hybrid: BM25=%d, Vector=%d, Fused=%d", 
                            len(bm25_results), len(vector_results), len(docs))
        else:
            docs = self.vector_store.search(self.embedder.embed_query(expanded), k=search_k)

        if not docs:
            return []

        # --- 癌种关键词加权重排序 ---
        if self.booster:
            docs = self.booster.boost(query, docs)

        # Filter by score threshold
        filtered = []
        for d in docs:
            # Skip ghost chunks with empty/broken metadata (ChromaDB corruption)
            if not d.metadata or len(d.metadata) <= 2:
                continue
            score = d.metadata.get("score", 0.0)
            rrf = d.metadata.get("rrf_score", 0.0)
            # Use the best available score for threshold check
            effective_score = max(score, rrf) if self.hybrid_config.enabled else score
            if effective_score >= self.retrieval_config.score_threshold:
                filtered.append(d)
        return filtered

    def update_config(self, retrieval_config: RetrievalConfig, hybrid_config: HybridSearchConfig) -> None:
        """Update retriever configuration (hot-reload)."""
        self.retrieval_config = retrieval_config
        self.hybrid_config = hybrid_config

    def set_expander(self, expander) -> None:
        """Set query expander (hot-reload)."""
        self.expander = expander

    def update_booster(self, booster_config: CancerBoostingConfig | None) -> None:
        """Update cancer booster (hot-reload)."""
        self.booster = CancerTypeBooster(booster_config) if booster_config else None

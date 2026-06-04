"""Embedding model management using sentence-transformers."""
import logging
import os
import threading

# Use HuggingFace mirror for users in regions where huggingface.co is unreachable
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# Avoid network requests during model loading if model is already cached
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import EmbeddingConfig
from app.core.interfaces import Embedder

logger = logging.getLogger(__name__)

# Avoid OpenMP/PyTorch deadlocks when encode() is called from thread pools (e.g. asyncio.to_thread)
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")


class STEmbedder(Embedder):
    """Sentence-transformers based embedder.

    Thread-safe: embed() and embed_query() are protected by a lock because
    SentenceTransformer models are NOT thread-safe under PyTorch.
    """

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            import torch
            # Safety: fall back to CPU if CUDA was requested but PyTorch has no CUDA support
            device = self.config.device
            if device != "cpu" and not torch.cuda.is_available():
                logger.warning(
                    "Embedding device '%s' requested but CUDA is not available "
                    "(PyTorch may be CPU-only). Falling back to CPU.",
                    device,
                )
                device = "cpu"
            logger.info("Loading embedding model: %s on %s", self.config.model_name, device)
            # Force single-thread to prevent deadlocks in thread-pool execution
            torch.set_num_threads(1)
            self._model = SentenceTransformer(self.config.model_name, device=device)
            logger.info("Embedding model loaded on %s (threads=1)", device)
        return self._model

    def reload_model(self, new_config: EmbeddingConfig) -> None:
        """Hot-reload embedding model if config changed."""
        if new_config == self.config:
            return
        logger.info("Hot-reloading embedding model: %s -> %s", self.config.model_name, new_config.model_name)
        with self._lock:
            if self._model is not None:
                del self._model
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            self._model = None
        self.config = new_config

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            model = self._load()
            embeddings = model.encode(
                texts,
                normalize_embeddings=self.config.normalize_embeddings,
                show_progress_bar=len(texts) > 100,
            )
            return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        embeddings = self.embed([query])
        return embeddings[0] if embeddings else []

    @property
    def dimension(self) -> int:
        model = self._load()
        return model.get_sentence_embedding_dimension() or 1024

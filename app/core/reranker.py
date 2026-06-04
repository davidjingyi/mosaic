"""Cross-encoder reranker using sentence-transformers."""
import logging
import os

# Avoid network requests during model loading if model is already cached
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from langchain_core.documents import Document
from sentence_transformers import CrossEncoder

from app.config import RetrievalConfig
from app.core.interfaces import Reranker

logger = logging.getLogger(__name__)


class BGEReranker(Reranker):
    """BGE CrossEncoder reranker."""

    def __init__(self, config: RetrievalConfig):
        self.config = config
        self._model: CrossEncoder | None = None

    def _load(self) -> CrossEncoder | None:
        if self._model is None and self.config.use_rerank:
            try:
                logger.info("Loading reranker: %s", self.config.rerank_model)
                self._model = CrossEncoder(self.config.rerank_model)
                logger.info("Reranker loaded")
            except Exception as e:
                logger.error("Failed to load reranker: %s", e)
        return self._model

    def rerank(self, query: str, docs: list[Document], top_k: int) -> list[Document]:
        model = self._load()
        if model is None or len(docs) <= 1:
            return docs[:top_k]

        contents = []
        for doc in docs:
            original = doc.metadata.get("original_content", "")
            contents.append(original if original else doc.page_content)

        pairs = [[query, c] for c in contents]
        scores = model.predict(pairs)
        for doc, score in zip(docs, scores):
            doc.metadata["rerank_score"] = float(score)

        ranked = sorted(docs, key=lambda d: d.metadata.get("rerank_score", 0), reverse=True)
        return ranked[:top_k]

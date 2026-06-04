"""Tests for hybrid retriever."""
from langchain_core.documents import Document

from app.config import HybridSearchConfig, RetrievalConfig
from app.core.retriever import _rrf_fusion


def test_rrf_fusion_basic():
    docs = [Document(page_content=f"doc {i}", metadata={"doc_id": "a", "chunk_index": i}) for i in range(3)]
    bm25 = [(docs[0], 1.0), (docs[1], 0.8)]
    vector = [(docs[1], 0.9), (docs[2], 0.7)]
    result = _rrf_fusion(bm25, vector, rrf_k=60, bm25_weight=0.4, vector_weight=0.6)
    assert len(result) == 3
    # doc1 appears in both lists, should rank highest
    assert result[0].metadata["chunk_index"] == 1

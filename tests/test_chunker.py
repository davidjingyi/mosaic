"""Tests for document chunker."""
from pathlib import Path

from langchain_core.documents import Document

from app.config import ChunkingConfig, DocTypeRule
from app.core.chunker import DefaultChunker


def test_default_chunking():
    config = ChunkingConfig(chunk_size=50, chunk_overlap=10)
    chunker = DefaultChunker(config)
    docs = [Document(page_content="这是一段很长的中文文本，用于测试分块功能。" * 10)]
    chunks = chunker.chunk(docs)
    assert len(chunks) > 1
    assert "chunk_index" in chunks[0].metadata


def test_doc_type_rule():
    config = ChunkingConfig(chunk_size=100, chunk_overlap=20)
    rules = [DocTypeRule(keywords=["指南"], chunk_size=20, chunk_overlap=5)]
    chunker = DefaultChunker(config, rules)
    docs = [Document(page_content="指南内容" * 50, metadata={"source": "CSCO指南.pdf"})]
    chunks = chunker.chunk(docs, file_path=Path("CSCO指南.pdf"))
    # With smaller chunk size, should produce more chunks
    assert len(chunks) >= 3


def test_contextual_chunking_disabled():
    config = ChunkingConfig(enable_contextual_chunking=False)
    chunker = DefaultChunker(config)
    docs = [Document(page_content="Hello world")]
    chunks = chunker.chunk(docs)
    assert chunks[0].page_content == "Hello world"

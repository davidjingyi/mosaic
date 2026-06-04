"""Tests for RAG engine."""
import pytest


@pytest.mark.asyncio
async def test_chat_pipeline(engine):
    chunks = []
    async for chunk in engine.chat("test query", stream=False):
        chunks.append(chunk)
    result = "".join(chunks)
    assert "mock answer" in result


@pytest.mark.asyncio
async def test_chat_cache(engine):
    # First call populates cache
    async for _ in engine.chat("cached query", stream=False):
        pass
    # Second call should hit cache
    chunks = []
    async for chunk in engine.chat("cached query", stream=False):
        chunks.append(chunk)
    result = "".join(chunks)
    assert "mock answer" in result


def test_delete_document(engine):
    # Empty store should return False
    assert engine.delete_document("nonexistent") is False

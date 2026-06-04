"""Tests for semantic cache."""
from app.config import CacheConfig
from app.core.semantic_cache import DefaultSemanticCache


class MockEmbedder:
    def embed_query(self, query: str) -> list[float]:
        # Return same embedding for similar queries
        return [0.1, 0.2, 0.3]


def test_cache_hit_miss():
    config = CacheConfig(enabled=True, max_size=10, similarity_threshold=0.9)
    cache = DefaultSemanticCache(config, embedder=MockEmbedder())
    
    # Put
    cache.put("query1", "answer1", [])
    
    # Hit with same embedding
    result, emb = cache.get("query1")
    assert result is not None
    assert result[0] == "answer1"
    assert emb is not None
    
    # Miss with different embedding (but our mock returns same, so force miss by threshold)
    cache.config.similarity_threshold = 0.999
    result2, emb2 = cache.get("query2")
    assert result2 is None
    assert emb2 is not None  # embedding still returned on miss
    
    cache.stop()


def test_cache_ttl():
    config = CacheConfig(enabled=True, max_size=10, similarity_threshold=0.9, default_ttl=0)
    cache = DefaultSemanticCache(config, embedder=MockEmbedder())
    cache.put("q", "a", [], ttl=0)
    import time
    time.sleep(0.1)
    result, emb = cache.get("q")
    assert result is None  # Expired
    cache.stop()


def test_cache_invalidate():
    config = CacheConfig(enabled=True, max_size=10)
    cache = DefaultSemanticCache(config, embedder=MockEmbedder())
    cache.put("q1", "a1", [])
    cache.put("q2", "a2", [])
    removed = cache.invalidate()
    assert removed == 2
    cache.stop()

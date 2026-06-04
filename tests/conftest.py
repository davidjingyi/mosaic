"""pytest fixtures."""
import pytest

from app.config import Settings
from app.core.chunker import DefaultChunker
from app.core.document_parser import DefaultDocumentParser
from app.core.embedder import STEmbedder
from app.core.engine import RAGEngine
from app.core.generator import OpenAIGenerator
from app.core.query_expander import MedicalQueryExpander
from app.core.retriever import HybridRetriever
from app.core.semantic_cache import DefaultSemanticCache
from app.core.vector_store import ChromaVectorStore


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def mock_embedder():
    """Mock embedder that returns deterministic embeddings."""
    class MockEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, query: str) -> list[float]:
            return [1.0, 0.0, 0.0]

        @property
        def dimension(self) -> int:
            return 3

    return MockEmbedder()


@pytest.fixture
def memory_vector_store(mock_embedder, tmp_path):
    store = ChromaVectorStore(str(tmp_path / "chroma"), mock_embedder)
    return store


@pytest.fixture
def mock_generator():
    class MockGenerator:
        async def generate(self, query, contexts, history=None):
            yield "mock answer"

        async def generate_non_stream(self, query, contexts, history=None):
            return "mock answer"

    return MockGenerator()


@pytest.fixture
def engine(settings, mock_embedder, memory_vector_store, mock_generator):
    parser = DefaultDocumentParser()
    chunker = DefaultChunker(settings.chunking, settings.doc_type_rules)
    retriever = HybridRetriever(
        memory_vector_store,
        mock_embedder,
        settings.retrieval,
        settings.hybrid_search,
        expander=None,
    )
    cache = DefaultSemanticCache(settings.cache, embedder=mock_embedder)
    return RAGEngine(
        settings=settings,
        parser=parser,
        chunker=chunker,
        embedder=mock_embedder,
        vector_store=memory_vector_store,
        retriever=retriever,
        reranker=None,
        expander=None,
        generator=mock_generator,
        cache=cache,
    )

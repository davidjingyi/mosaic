"""Core abstractions for the RAG system.

All components implement Protocols so they can be swapped without changing
 the engine orchestration code.
"""
from pathlib import Path
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from langchain_core.documents import Document


@runtime_checkable
class DocumentParser(Protocol):
    """Parse a file into LangChain Document objects."""

    def parse(self, file_path: Path) -> list[Document]: ...


@runtime_checkable
class Chunker(Protocol):
    """Split documents into chunks."""

    def chunk(self, documents: list[Document], file_path: Path | None = None) -> list[Document]: ...


@runtime_checkable
class Embedder(Protocol):
    """Generate vector embeddings for texts."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...

    @property
    def dimension(self) -> int: ...


@runtime_checkable
class VectorStore(Protocol):
    """Vector database for similarity search."""

    def add(self, docs: list[Document], doc_id: str) -> list[str]: ...

    def search(
        self, query_vec: list[float], k: int, filters: dict[str, Any] | None = None
    ) -> list[Document]: ...

    def delete(self, doc_id: str) -> int: ...

    def count(self) -> int: ...

    def get_all(self) -> list[dict[str, Any]]: ...

    def get_chunks(self, doc_id: str) -> list[dict[str, Any]]: ...

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None: ...

    def update_chunk(self, chunk_id: str, document: str | None = None, metadata: dict[str, Any] | None = None) -> bool: ...

    def delete_chunk(self, chunk_id: str) -> bool: ...


@runtime_checkable
class Retriever(Protocol):
    """Retrieve relevant documents for a query."""

    def retrieve(self, query: str) -> list[Document]: ...

    def add_documents(self, documents: list[Document]) -> None: ...

    def delete_documents(self, doc_id: str) -> None: ...

    def remove_chunk(self, chunk_id: str) -> bool: ...

    def update_chunk(self, chunk_id: str, new_text: str) -> bool: ...


@runtime_checkable
class Reranker(Protocol):
    """Rerank retrieved documents."""

    def rerank(self, query: str, docs: list[Document], top_k: int) -> list[Document]: ...


@runtime_checkable
class Generator(Protocol):
    """Generate responses from an LLM."""

    async def generate(
        self,
        query: str,
        contexts: list[str],
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[str, None]: ...

    async def generate_non_stream(
        self,
        query: str,
        contexts: list[str],
        history: list[dict[str, Any]] | None = None,
    ) -> str: ...


@runtime_checkable
class QueryExpander(Protocol):
    """Expand queries with synonyms or medical terms."""

    def expand(self, query: str) -> str: ...


@runtime_checkable
class SemanticCache(Protocol):
    """Cache query results by semantic similarity."""

    def get(
        self, query: str, query_embedding: list[float] | None = None
    ) -> tuple[tuple[str, list[dict[str, Any]]] | None, list[float] | None]: ...

    def put(
        self,
        query: str,
        answer: str,
        sources: list[dict[str, Any]],
        query_embedding: list[float] | None = None,
        ttl: int | None = None,
    ) -> None: ...

    def invalidate(self, pattern: str | None = None) -> int: ...

    def get_stats(self) -> dict[str, Any]: ...

    def stop(self) -> None: ...

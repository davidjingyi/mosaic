"""RAG Engine: orchestrates the full pipeline using pluggable components."""
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from typing import Any, AsyncGenerator

from langchain_core.documents import Document

from app.config import Settings
from app.core.interfaces import (
    Chunker,
    DocumentParser,
    Embedder,
    Generator,
    QueryExpander,
    Reranker,
    Retriever,
    SemanticCache,
    VectorStore,
)
from app.models.schemas import DocumentInfo

logger = logging.getLogger(__name__)


class RAGEngine:
    """Orchestrates document processing, retrieval, and generation."""

    def __init__(
        self,
        settings: Settings,
        parser: DocumentParser,
        chunker: Chunker,
        embedder: Embedder,
        vector_store: VectorStore,
        retriever: Retriever,
        reranker: Reranker | None,
        expander: QueryExpander | None,
        generator: Generator,
        cache: SemanticCache,
    ):
        self.settings = settings
        self.parser = parser
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.retriever = retriever
        self.reranker = reranker
        self.expander = expander
        self.generator = generator
        self.cache = cache

    async def chat(
        self,
        query: str,
        history: list[dict[str, Any]] | None = None,
        stream: bool = True,
        session_id: str | None = None,
        prompt_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Main chat entry: cache check -> retrieve -> generate."""
        # 1. Check cache
        cache_result, query_emb = self.cache.get(query)
        if cache_result is not None:
            cached_answer, cached_sources = cache_result
            logger.info("[Cache HIT] query='%s...'", query[:40])
            result = {
                "answer": cached_answer,
                "sources": cached_sources,
                "cached": True,
                "session_id": session_id,
            }
            if stream:
                yield json.dumps({"chunk": cached_answer, "done": False, **{k: v for k, v in result.items() if k != "answer"}}, ensure_ascii=False)
                yield json.dumps({"done": True}, ensure_ascii=False)
            else:
                yield json.dumps(result, ensure_ascii=False)
            return

        # 2. Retrieve
        logger.debug("[Cache MISS] query='%s...' | running full RAG", query[:40])
        try:
            docs = await asyncio.to_thread(self.retriever.retrieve, query)
            # Check if cancer boosting was applied
            any_boosted = any(
                d.metadata.get("cancer_boosted") for d in docs
            ) if docs else False
            # 2.5 Rerank if enabled (skip when cancer boosted — booster already handled ordering)
            if self.reranker and self.settings.retrieval.use_rerank and not any_boosted:
                docs = await asyncio.to_thread(
                    self.reranker.rerank, query, docs, self.settings.retrieval.rerank_top_k
                )
            contexts = [doc.page_content for doc in docs]
            sources = [
                {
                    "content": doc.page_content[:300],
                    "metadata": {k: str(v)[:100] for k, v in doc.metadata.items()},
                    "score": doc.metadata.get("rerank_score", doc.metadata.get("score", 0.0)),
                }
                for doc in docs[:self.settings.retrieval.rerank_top_k]
            ]

            # 3. Generate
            if stream:
                full_answer: list[str] = []
                async for chunk in self.generator.generate(query, contexts, history, prompt_id):
                    full_answer.append(chunk)
                    yield json.dumps({"chunk": chunk, "done": False}, ensure_ascii=False)
                answer = "".join(full_answer)
                yield json.dumps({"done": True, "sources": sources, "session_id": session_id}, ensure_ascii=False)
                self.cache.put(query, answer, sources, query_embedding=query_emb)
            else:
                answer = await self.generator.generate_non_stream(query, contexts, history, prompt_id)
                yield json.dumps(
                    {"answer": answer, "sources": sources, "cached": False, "session_id": session_id},
                    ensure_ascii=False,
                )
                self.cache.put(query, answer, sources, query_embedding=query_emb)
        except httpx.HTTPError as e:
            logger.error("LLM API error: %s", e)
            yield json.dumps({"error": "llm_unreachable", "detail": str(e)}, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.error("JSON parse error: %s", e)
            yield json.dumps({"error": "parse_error", "detail": str(e)}, ensure_ascii=False)
        except Exception as e:
            logger.exception("Unexpected RAG engine error: %s", e)
            yield json.dumps({"error": "internal_error", "detail": str(e) if os.environ.get("DEBUG") == "true" else "服务器内部错误"}, ensure_ascii=False)

    def preview_document(self, file_path: str) -> list[Document]:
        """Parse and chunk a document without embedding.
        
        Returns the list of chunks with metadata.
        """
        path_obj = Path(file_path)
        documents = self.parser.parse(path_obj)
        chunks = self.chunker.chunk(documents, file_path=path_obj)
        return chunks

    def embed_document(self, chunks: list[Document], doc_id: str, progress_callback=None) -> int:
        """Embed chunks and add to vector store + BM25 index.

        Args:
            chunks: Document chunks to embed.
            doc_id: Document ID.
            progress_callback: Optional callback(chunk_current, chunk_total, doc_id).

        Returns the number of chunks indexed.
        """
        for chunk in chunks:
            chunk.metadata["doc_id"] = doc_id
        try:
            chunk_count = len(self.vector_store.add(chunks, doc_id, progress_callback=progress_callback))
            self.retriever.add_documents(chunks)
            return chunk_count
        except Exception:
            # Rollback: remove from vector store if BM25 failed
            try:
                self.vector_store.delete(doc_id)
            except Exception:
                pass
            raise

    async def ingest_document(self, file_path: str, doc_id: str) -> DocumentInfo:
        """Process and index a single document.

        Clears old data first to avoid orphans and duplicates.
        If embedding fails, old data is already lost — caller must handle recovery.
        """
        import os
        from datetime import datetime

        # 1. Parse and chunk first (validate document before touching store)
        chunks = self.preview_document(file_path)

        # 2. Embed and index (atomic-ish: both vector + BM25 or neither)
        try:
            chunk_count = self.embed_document(chunks, doc_id)
        except Exception:
            # Clean up any partial vector data
            try:
                self.vector_store.delete(doc_id)
            except Exception:
                pass
            raise

        # 3. Only delete old data after new data is successfully indexed
        #    (In current design doc_id is reused, so old data was already overwritten
        #     by vector_store.add which pre-deletes by doc_id.)
        path_obj = Path(file_path)
        file_size = os.path.getsize(file_path) if path_obj.exists() else 0
        return DocumentInfo(
            doc_id=doc_id,
            filename=path_obj.name,
            upload_time=datetime.now(),
            chunk_count=chunk_count,
            file_size=file_size,
        )

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from vector store and BM25 index."""
        try:
            count = self.vector_store.delete(doc_id)
            self.retriever.delete_documents(doc_id)
            return count > 0
        except Exception as e:
            logger.error("Error deleting document %s: %s", doc_id, e)
            return False

    def get_all_documents(self) -> list[DocumentInfo]:
        from datetime import datetime
        import os

        documents = []
        # Batch scan upload directory to avoid per-document iterdir overhead
        upload_root = Path("data/uploads")
        upload_map: dict[str, tuple[str, int, float]] = {}
        if upload_root.exists():
            for entry in os.scandir(upload_root):
                if entry.is_dir():
                    doc_id = entry.name
                    try:
                        with os.scandir(entry.path) as inner:
                            for f in inner:
                                if f.is_file():
                                    stat = f.stat()
                                    upload_map[doc_id] = (f.name, stat.st_size, stat.st_ctime)
                                    break
                    except OSError:
                        pass

        for item in self.vector_store.get_all():
            doc_id = item["doc_id"]
            chunk_count = item["chunk_count"]
            filename = doc_id
            file_size = 0
            upload_time = None
            if doc_id in upload_map:
                filename, file_size, ctime = upload_map[doc_id]
                upload_time = datetime.fromtimestamp(ctime)
            documents.append(
                DocumentInfo(
                    doc_id=doc_id,
                    filename=filename,
                    upload_time=upload_time,
                    chunk_count=chunk_count,
                    file_size=file_size,
                )
            )
        return documents

    def retrieve_debug(self, query: str, k: int | None = None) -> list[Document]:
        search_k = k or self.settings.retrieval.search_k
        query_vec = self.embedder.embed_query(query)
        return self.vector_store.search(query_vec, k=search_k)

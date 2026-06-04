"""ChromaDB vector store implementation."""
import logging
import threading
import uuid
from typing import Any

import chromadb
from langchain_core.documents import Document

from app.config import Settings
from app.core.interfaces import Embedder, VectorStore

logger = logging.getLogger(__name__)


class ChromaVectorStore(VectorStore):
    """ChromaDB-backed vector store with cosine similarity.

    Thread-safe: all operations are protected by a lock because
    chromadb.PersistentClient is not thread-safe.
    """

    def __init__(self, persist_dir: str, embedder: Embedder):
        self.persist_dir = persist_dir
        self.embedder = embedder
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name="rag_documents",
            metadata={"hnsw:space": "cosine"},
        )
        self._lock = threading.RLock()
        self._migrate_old_chunks()

    def _migrate_old_chunks(self) -> None:
        """Migrate old chunks that don't have chunk_id in metadata."""
        try:
            results = self._collection.get(include=["metadatas"])
            if not results or not results["ids"]:
                return
            ids_to_update = []
            metadatas_to_update = []
            for i, cid in enumerate(results["ids"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                if meta and "chunk_id" not in meta:
                    meta["chunk_id"] = cid
                    ids_to_update.append(cid)
                    metadatas_to_update.append(meta)
            if ids_to_update:
                self._collection.update(ids=ids_to_update, metadatas=metadatas_to_update)
                logger.info("Migrated %d old chunks to new format", len(ids_to_update))
        except (OSError, ValueError) as e:
            # Disk I/O or data errors are serious
            logger.error("Chunk migration failed due to I/O or data error: %s", e)
            raise
        except Exception as e:
            logger.warning("Chunk migration skipped: %s", e)

    def add(self, docs: list[Document], doc_id: str, progress_callback=None) -> list[str]:
        if not docs:
            return []

        texts = [d.page_content for d in docs]
        ids = [str(uuid.uuid4()) for _ in range(len(docs))]
        metadatas = []
        for i, doc in enumerate(docs):
            meta = dict(doc.metadata)
            meta["doc_id"] = doc_id
            meta["chunk_index"] = i
            meta["chunk_id"] = ids[i]
            metadatas.append(meta)
            doc.metadata["chunk_id"] = ids[i]
            doc.metadata["chunk_index"] = i
            doc.metadata["doc_id"] = doc_id

        with self._lock:
            # Delete old chunks for this doc_id
            try:
                self._collection.delete(where={"doc_id": doc_id})
            except Exception as e:
                logger.debug("Pre-delete for doc %s: %s", doc_id, e)

            total = len(texts)
            # Embed in small batches to avoid overwhelming the embedding service
            embed_batch = 8
            upsert_batch = 8
            for i in range(0, len(texts), upsert_batch):
                end = min(i + upsert_batch, len(texts))
                batch_texts = texts[i:end]
                # Embed this batch
                embeddings = self.embedder.embed(batch_texts)
                self._collection.upsert(
                    ids=ids[i:end],
                    documents=batch_texts,
                    embeddings=embeddings,
                    metadatas=metadatas[i:end],
                )
                if progress_callback:
                    progress_callback(end, total, doc_id)
            return ids

    def search(
        self, query_vec: list[float], k: int, filters: dict[str, Any] | None = None
    ) -> list[Document]:
        with self._lock:
            results = self._collection.query(
                query_embeddings=[query_vec],
                n_results=k,
                where=filters,
                include=["documents", "metadatas", "distances"],
            )
            documents = []
            if results and results["documents"]:
                for i, text in enumerate(results["documents"][0]):
                    text = text or ""
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                    metadata = metadata or {}
                    # Skip ghost chunks with empty metadata (ChromaDB segment corruption)
                    if not metadata:
                        continue
                    distance = results["distances"][0][i] if results["distances"] else 0
                    metadata["score"] = 1.0 - float(distance)
                    documents.append(Document(page_content=text, metadata=metadata))
            return documents

    def delete(self, doc_id: str) -> int:
        with self._lock:
            try:
                results = self._collection.get(where={"doc_id": doc_id})
                if results and results["ids"]:
                    self._collection.delete(where={"doc_id": doc_id})
                    return len(results["ids"])
            except OSError as e:
                logger.error("Disk error deleting document %s: %s", doc_id, e)
                raise
            except Exception as e:
                logger.error("Error deleting document %s: %s", doc_id, e)
            return 0

    def count(self) -> int:
        with self._lock:
            try:
                return self._collection.count()
            except OSError as e:
                logger.error("Disk error counting documents: %s", e)
                raise
            except Exception as e:
                logger.error("Error counting documents: %s", e)
                return 0

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                results = self._collection.get(include=["metadatas"])
                if not results or not results["metadatas"]:
                    return []
                doc_map: dict[str, int] = {}
                for meta in results["metadatas"]:
                    did = meta.get("doc_id", "unknown")
                    doc_map[did] = doc_map.get(did, 0) + 1
                return [{"doc_id": did, "chunk_count": cnt} for did, cnt in doc_map.items()]
            except Exception as e:
                logger.error("Error getting all documents: %s", e)
                return []

    def get_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        """Get all chunks for a document."""
        with self._lock:
            try:
                results = self._collection.get(
                    where={"doc_id": doc_id},
                    include=["documents", "metadatas"],
                )
                chunks = []
                if results and results["ids"]:
                    for i, cid in enumerate(results["ids"]):
                        chunks.append({
                            "chunk_id": cid,
                            "document": results["documents"][i] if results.get("documents") else "",
                            "metadata": results["metadatas"][i] if results.get("metadatas") else {},
                        })
                # Sort by chunk_index
                chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
                return chunks
            except Exception as e:
                logger.error("Error getting chunks for doc %s: %s", doc_id, e)
                return []

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """Get a single chunk by its UUID."""
        with self._lock:
            try:
                results = self._collection.get(
                    ids=[chunk_id],
                    include=["documents", "metadatas"],
                )
                if results and results["ids"]:
                    return {
                        "chunk_id": results["ids"][0],
                        "document": results["documents"][0] if results.get("documents") else "",
                        "metadata": results["metadatas"][0] if results.get("metadatas") else {},
                    }
            except Exception as e:
                logger.error("Error getting chunk %s: %s", chunk_id, e)
            return None

    def update_chunk(self, chunk_id: str, document: str | None = None, metadata: dict[str, Any] | None = None) -> bool:
        """Update a chunk. If document text changes, re-embed."""
        with self._lock:
            try:
                existing = self.get_chunk(chunk_id)
                if not existing:
                    return False

                update_doc = document if document is not None else existing["document"]
                update_meta = dict(existing["metadata"])
                if metadata:
                    update_meta.update(metadata)

                # If text changed, recompute embedding
                if document is not None and document != existing["document"]:
                    embeddings = self.embedder.embed([document])
                    self._collection.update(
                        ids=[chunk_id],
                        documents=[update_doc],
                        embeddings=embeddings,
                        metadatas=[update_meta],
                    )
                else:
                    # Only update metadata; do NOT pass documents without embeddings
                    # or ChromaDB will try to auto-embed with a default 384-dim model
                    self._collection.update(
                        ids=[chunk_id],
                        metadatas=[update_meta],
                    )
                return True
            except Exception as e:
                logger.error("Error updating chunk %s: %s", chunk_id, e)
                return False

    def delete_chunk(self, chunk_id: str) -> bool:
        """Delete a single chunk by its UUID."""
        with self._lock:
            try:
                self._collection.delete(ids=[chunk_id])
                return True
            except Exception as e:
                logger.error("Error deleting chunk %s: %s", chunk_id, e)
                return False

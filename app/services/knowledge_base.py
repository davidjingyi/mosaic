"""Knowledge base scanner for auto-importing documents."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

from app.config import KnowledgeBaseConfig
from app.models.schemas import ScanResult
from app.services import document_registry as registry

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".pdf", ".md"}


class KnowledgeBaseScanner:
    """Scan knowledge base directories and manage document index."""

    def __init__(self, config: KnowledgeBaseConfig, engine):
        self.config = config
        self.engine = engine

    def _scan_dir(self, directory: str, extensions: set[str]) -> list[str]:
        files = []
        if not os.path.exists(directory):
            logger.warning("Directory does not exist: %s", directory)
            return files
        for root, _, filenames in os.walk(directory):
            for f in filenames:
                if any(f.lower().endswith(ext) for ext in extensions):
                    files.append(os.path.join(root, f))
        return sorted(files)

    def _file_hash(self, file_path: str) -> str:
        hasher = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            hasher.update(str(os.path.getmtime(file_path)).encode())
        except OSError as e:
            logger.warning("Cannot hash %s: %s", file_path, e)
        return hasher.hexdigest()

    def _estimate_chunks(self, file_path: str) -> int:
        """Rough estimate of chunks based on file size."""
        try:
            size = os.path.getsize(file_path)
            # Assume ~650 bytes effective per chunk (chunk_size=1000, overlap=200)
            return max(1, size // 650)
        except OSError:
            return 0

    async def preview_scan(self) -> list[dict]:
        """Preview scan: detect new/modified/unchanged/missing files without processing."""
        all_files = []
        for p in self.config.pdf_paths:
            if os.path.exists(p):
                all_files.extend((f, "pdf") for f in self._scan_dir(p, {".pdf"}))
        for p in self.config.markdown_paths:
            if os.path.exists(p):
                all_files.extend((f, "md") for f in self._scan_dir(p, {".md"}))

        results = []
        scanned_doc_ids = set()
        existing_docs = registry.get_documents(source="kb")
        existing_map = {d.get("file_path"): d for d in existing_docs}
        for file_path, _ in all_files:
            file_hash = self._file_hash(file_path)
            existing = existing_map.get(file_path)
            if existing:
                scanned_doc_ids.add(existing["doc_id"])
                if existing.get("file_hash") == file_hash:
                    results.append({
                        "file_path": file_path,
                        "status": "unchanged",
                        "file_size": existing.get("file_size", 0),
                        "existing_doc_id": existing["doc_id"],
                        "expected_chunks_estimate": existing.get("chunk_count", 0),
                    })
                else:
                    results.append({
                        "file_path": file_path,
                        "status": "modified",
                        "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                        "existing_doc_id": existing["doc_id"],
                        "expected_chunks_estimate": self._estimate_chunks(file_path),
                    })
            else:
                results.append({
                    "file_path": file_path,
                    "status": "new",
                    "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                    "existing_doc_id": None,
                    "expected_chunks_estimate": self._estimate_chunks(file_path),
                })

        # Detect missing files (in registry but not on disk)
        all_kb_docs = registry.get_documents(source="kb")
        for doc in all_kb_docs:
            if doc["doc_id"] not in scanned_doc_ids:
                results.append({
                    "file_path": doc["file_path"],
                    "status": "missing",
                    "file_size": doc.get("file_size", 0),
                    "existing_doc_id": doc["doc_id"],
                    "expected_chunks_estimate": doc.get("chunk_count", 0),
                })

        return results

    def preview_chunks_sync(
        self,
        file_paths: list[str],
        folder_id: str | None = None,
        progress_callback: callable | None = None,
    ) -> list[dict]:
        """Parse and chunk files without embedding (synchronous, thread-safe).

        Chunks are cached to disk after parsing — subsequent ingest runs skip
        re-parsing if the file hash matches, avoiding duplicate work.
        """
        results = []
        total = len(file_paths)
        # Query registry once outside the loop to avoid O(N^2) deep copies
        existing_docs = registry.get_documents(source="kb")
        existing_map = {d.get("file_path"): d for d in existing_docs}

        # Ensure chunks cache directory exists (avoids re-chunking during ingest)
        chunks_dir = Path("data/chunks")
        chunks_dir.mkdir(parents=True, exist_ok=True)

        for idx, file_path in enumerate(file_paths, start=1):
            file_name = os.path.basename(file_path)

            if progress_callback:
                try:
                    progress_callback("scanning", idx, total, file_name)
                except RuntimeError as e:
                    if "cancelled" in str(e).lower():
                        break

            if not os.path.exists(file_path):
                results.append({"doc_id": "", "file_path": file_path, "chunk_count": 0, "chunks": [], "error": "File not found"})
                continue

            # Find or create doc_id
            existing = existing_map.get(file_path)
            doc_id = existing["doc_id"] if existing else str(uuid.uuid4())[:12]

            # Register if new
            if not existing:
                registry.register_document(
                    doc_id=doc_id,
                    file_path=file_path,
                    source="kb",
                    folder_id=folder_id,
                    file_hash=self._file_hash(file_path),
                    file_size=os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                    status="pending",
                )
            else:
                registry.update_document(doc_id, folder_id=folder_id, status="pending")

            try:
                chunks = self.engine.preview_document(file_path)

                # Save chunks to disk cache so ingest can skip re-parsing
                current_hash = self._file_hash(file_path)
                try:
                    cache_path = chunks_dir / f"{doc_id}.json"
                    cache_data = {
                        "file_hash": current_hash,
                        "chunks": [{"content": c.page_content, "metadata": dict(c.metadata)} for c in chunks],
                    }
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache_data, f, ensure_ascii=False)
                except Exception:
                    logger.debug("Failed to save chunk cache for %s", doc_id)

                if progress_callback:
                    progress_callback("chunking", idx, total, file_name)

                chunk_preview = []
                for i, chunk in enumerate(chunks[:5]):
                    chunk_preview.append({
                        "index": i,
                        "text": chunk.page_content[:500],
                        "metadata": dict(chunk.metadata),
                    })

                registry.update_document(
                    doc_id,
                    status="chunked",
                    chunk_count=len(chunks),
                    chunk_preview=chunk_preview,
                    file_hash=self._file_hash(file_path),
                )
                results.append({
                    "doc_id": doc_id,
                    "file_path": file_path,
                    "chunk_count": len(chunks),
                    "chunks": chunk_preview,
                })
            except RuntimeError as e:
                if "cancelled" in str(e).lower():
                    registry.update_document(doc_id, status="failed", error_msg="Cancelled by user")
                    results.append({"doc_id": doc_id, "file_path": file_path, "chunk_count": 0, "chunks": [], "error": "Cancelled by user"})
                    break
            except Exception as e:
                logger.error("Preview chunks failed for %s: %s", file_path, e)
                registry.update_document(doc_id, status="failed", error_msg=str(e))
                results.append({
                    "doc_id": doc_id,
                    "file_path": file_path,
                    "chunk_count": 0,
                    "chunks": [],
                    "error": str(e),
                })
        return results

    async def preview_chunks(self, file_paths: list[str], folder_id: str | None = None) -> list[dict]:
        """Backward-compatible async wrapper."""
        return self.preview_chunks_sync(file_paths, folder_id)

    def ingest_documents_sync(
        self,
        doc_ids: list[str],
        progress_callback: callable | None = None,
    ) -> dict:
        """Embed and index selected documents (synchronous, safe to run in thread pool).

        Chunks are cached to disk after first parse — subsequent runs skip re-parsing
        if the file hash matches, making the embed flow idempotent and retry-safe.

        Args:
            doc_ids: List of document IDs to ingest.
            progress_callback: Optional callback(stage, current, total, file_name).
                               Should raise RuntimeError('cancelled') to stop early.
        """
        imported = 0
        updated = 0
        errors = []
        total = len(doc_ids)

        # Ensure chunks cache directory exists
        chunks_dir = Path("data/chunks")
        chunks_dir.mkdir(parents=True, exist_ok=True)

        for idx, doc_id in enumerate(doc_ids, start=1):
            # Check cancellation before each doc
            if progress_callback:
                try:
                    progress_callback("scanning", idx, total, "")
                except RuntimeError as e:
                    if "cancelled" in str(e).lower():
                        errors.append("Cancelled by user")
                        break

            doc = registry.get_document(doc_id)
            if not doc:
                errors.append(f"Doc {doc_id} not found in registry")
                if progress_callback:
                    progress_callback("scanning", idx, total, doc_id)
                continue
            file_path = doc["file_path"]
            file_name = os.path.basename(file_path)

            if not os.path.exists(file_path):
                errors.append(f"File not found: {file_path}")
                registry.update_document(doc_id, status="failed", error_msg="File not found")
                if progress_callback:
                    progress_callback("scanning", idx, total, file_name)
                continue

            try:
                if progress_callback:
                    progress_callback("scanning", idx, total, file_name)

                registry.update_document(doc_id, status="embedding")

                # Try loading chunks from cache (avoids re-parsing on retry)
                chunks = None
                cache_path = chunks_dir / f"{doc_id}.json"
                current_hash = self._file_hash(file_path)
                if cache_path.exists():
                    try:
                        with open(cache_path, "r", encoding="utf-8") as f:
                            cached = json.load(f)
                        if cached.get("file_hash") == current_hash:
                            from langchain_core.documents import Document
                            chunks = [Document(page_content=c["content"], metadata=c["metadata"]) for c in cached["chunks"]]
                            logger.info("Loaded %d chunks from cache for %s", len(chunks), doc_id)
                    except Exception:
                        logger.debug("Failed to load chunk cache for %s, will re-parse", doc_id)

                # Parse if cache miss
                if chunks is None:
                    chunks = self.engine.preview_document(file_path)
                    # Save to cache for future retries
                    try:
                        cache_data = {
                            "file_hash": current_hash,
                            "chunks": [{"content": c.page_content, "metadata": dict(c.metadata)} for c in chunks],
                        }
                        with open(cache_path, "w", encoding="utf-8") as f:
                            json.dump(cache_data, f, ensure_ascii=False)
                    except Exception:
                        logger.debug("Failed to save chunk cache for %s", doc_id)

                if progress_callback:
                    progress_callback("chunking", idx, total, file_name)

                # Embed with chunk-level progress
                def _chunk_progress(chunk_current, chunk_total, _doc_id):
                    if progress_callback:
                        progress_callback("embedding", idx, total, file_name, chunk_current, chunk_total)

                chunk_count = self.engine.embed_document(chunks, doc_id, progress_callback=_chunk_progress)

                if progress_callback:
                    progress_callback("indexing", idx, total, file_name)

                # Save chunk IDs for later reference
                chunk_ids = [c.metadata.get("chunk_id", "") for c in chunks]
                registry.update_document(
                    doc_id,
                    status="indexed",
                    chunk_count=chunk_count,
                    chunk_ids=chunk_ids,
                    file_hash=self._file_hash(file_path),
                    error_msg="",
                )
                if doc.get("status") in {"pending", "chunked", "failed"}:
                    imported += 1
                else:
                    updated += 1
            except RuntimeError as e:
                if "cancelled" in str(e).lower():
                    logger.warning("Ingest cancelled at doc %s", doc_id)
                    registry.update_document(doc_id, status="failed", error_msg="Cancelled by user")
                    errors.append(f"{doc_id}: Cancelled by user")
                    break
            except Exception as e:
                logger.error("Ingest failed for %s: %s", doc_id, e)
                errors.append(f"{doc_id}: {e}")
                registry.update_document(doc_id, status="failed", error_msg=str(e))

        return {
            "success": True,
            "imported": imported,
            "updated": updated,
            "errors": errors,
        }

    async def ingest_documents(
        self,
        doc_ids: list[str],
        progress_callback: callable | None = None,
    ) -> dict:
        """Backward-compatible async wrapper (blocking — use only in dedicated threads)."""
        return self.ingest_documents_sync(doc_ids, progress_callback)

    async def scan_all(self) -> ScanResult:
        """Legacy one-step scan. Deprecated in favor of preview + ingest."""
        result = ScanResult(
            success=True,
            scanned_paths=list(set(self.config.pdf_paths + self.config.markdown_paths)),
        )
        preview = await self.preview_scan()
        to_process = [p for p in preview if p["status"] in {"new", "modified"}]
        missing = [p for p in preview if p["status"] == "missing"]

        result.total_files_found = len(preview)
        result.removed = len(missing)

        # Remove missing files
        for p in missing:
            doc_id = p.get("existing_doc_id")
            if doc_id:
                try:
                    self.engine.delete_document(doc_id)
                    registry.delete_document(doc_id)
                except Exception as e:
                    logger.warning("Error removing %s: %s", doc_id, e)

        # Process new/modified
        file_paths = [p["file_path"] for p in to_process]
        preview_chunks = await self.preview_chunks(file_paths)
        ingest_result = await self.ingest_documents([p["doc_id"] for p in preview_chunks if not p.get("error")])

        result.imported = ingest_result["imported"]
        result.updated = ingest_result["updated"]
        result.errors = ingest_result["errors"]
        return result

    def get_stats(self) -> dict:
        return registry.get_stats()

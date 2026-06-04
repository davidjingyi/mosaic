"""Chunk management API routes."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.deps import get_engine, require_admin
from app.core.engine import RAGEngine
from app.models.schemas import ChunkUpdateRequest
from app.services import document_registry as registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chunks", tags=["片段管理"])


@router.get("")
async def list_chunks(doc_id: str, request: Request, skip: int = 0, limit: int = 100):
    """List all chunks for a document."""
    engine: RAGEngine = get_engine(request)
    chunks = engine.vector_store.get_chunks(doc_id)
    total = len(chunks)
    paginated = chunks[skip: skip + limit]
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "chunks": paginated,
    }


@router.get("/{chunk_id}")
async def get_chunk(chunk_id: str, request: Request):
    """Get a single chunk by its UUID."""
    engine: RAGEngine = get_engine(request)
    chunk = engine.vector_store.get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="片段不存在")
    return chunk


@router.put("/{chunk_id}")
async def update_chunk(chunk_id: str, request: Request, admin=Depends(require_admin)):
    """Update a chunk's content and/or metadata.
    
    If content changes, the embedding is recomputed automatically.
    BM25 index is also updated.
    """
    engine: RAGEngine = get_engine(request)
    existing = engine.vector_store.get_chunk(chunk_id)
    if not existing:
        raise HTTPException(status_code=404, detail="片段不存在")

    try:
        import json
        body_raw = await request.body()
        body = json.loads(body_raw)
        content = body.get("content")
        metadata = body.get("metadata")

        # Update in vector store
        ok = engine.vector_store.update_chunk(
            chunk_id,
            document=content,
            metadata=metadata,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="更新失败")

        # If content changed, also update BM25
        if content is not None:
            engine.retriever.update_chunk(chunk_id, content)

        # Update registry chunk_count if needed
        doc_id = existing["metadata"].get("doc_id")
        if doc_id:
            chunks = engine.vector_store.get_chunks(doc_id)
            registry.update_document(doc_id, chunk_count=len(chunks))

        return {"success": True, "chunk_id": chunk_id}
    except Exception as e:
        logger.error("Update chunk failed: %s", e)
        raise HTTPException(status_code=500, detail=f"更新失败: {e}")


@router.delete("/{chunk_id}")
async def delete_chunk(chunk_id: str, request: Request, admin=Depends(require_admin)):
    """Delete a single chunk."""
    engine: RAGEngine = get_engine(request)
    existing = engine.vector_store.get_chunk(chunk_id)
    if not existing:
        raise HTTPException(status_code=404, detail="片段不存在")

    try:
        doc_id = existing["metadata"].get("doc_id")
        # Delete from vector store FIRST (source of truth)
        engine.vector_store.delete_chunk(chunk_id)
        # Then remove from BM25
        engine.retriever.remove_chunk(chunk_id)

        # Update registry
        if doc_id:
            chunks = engine.vector_store.get_chunks(doc_id)
            registry.update_document(doc_id, chunk_count=len(chunks))

        return {"success": True}
    except Exception as e:
        logger.error("Delete chunk failed: %s", e)
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")


@router.post("/{chunk_id}/toggle")
async def toggle_chunk(chunk_id: str, request: Request, admin=Depends(require_admin)):
    """Toggle a chunk's enabled state."""
    engine: RAGEngine = get_engine(request)
    existing = engine.vector_store.get_chunk(chunk_id)
    if not existing:
        raise HTTPException(status_code=404, detail="片段不存在")

    meta = existing.get("metadata", {})
    enabled = not meta.get("enabled", True)
    engine.vector_store.update_chunk(chunk_id, metadata={"enabled": enabled})
    return {"success": True, "chunk_id": chunk_id, "enabled": enabled}

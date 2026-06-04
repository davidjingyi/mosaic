"""Knowledge base management API routes."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import get_engine, require_admin
from app.core.engine import RAGEngine
from app.models.schemas import (
    BatchActionRequest,
    FolderCreate,
    FolderUpdate,
    IngestRequest,
    PreviewChunksRequest,
)
from app.services import document_registry as registry
from app.services.knowledge_base import KnowledgeBaseScanner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge-base", tags=["知识库管理"])


class PathsRequest(BaseModel):
    pdf_paths: list[str] = Field(default_factory=list)
    markdown_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Background job runners
# ---------------------------------------------------------------------------

async def _run_embed(job_id: str, doc_ids: list[str], engine: RAGEngine, job_manager) -> None:
    """Run embedding/indexing in background."""
    import asyncio

    scanner = KnowledgeBaseScanner(engine.settings.knowledge_base, engine)
    callback = job_manager.make_progress_callback(job_id)

    job_manager.update_job(job_id, status="running", current_stage="scanning")
    try:
        result = await asyncio.to_thread(
            scanner.ingest_documents_sync, doc_ids, callback
        )
        if job_manager.is_cancelled(job_id):
            job_manager.update_job(job_id, status="cancelled", error="Cancelled by user")
            # Rollback document statuses
            for doc_id in doc_ids:
                registry.update_document(
                    doc_id,
                    status="chunked",
                    error_msg="Embedding cancelled by user",
                )
        else:
            job_manager.update_job(
                job_id,
                status="completed",
                progress_pct=100,
                current_stage="completed",
            )
            job = job_manager.get_job(job_id)
            if job:
                job["result"] = result
                job_manager._save()
    except Exception as e:
        logger.error("Embed job %s failed: %s", job_id, e)
        job_manager.update_job(job_id, status="failed", error=str(e))
        # Rollback document statuses so they can be retried
        for doc_id in doc_ids:
            registry.update_document(
                doc_id,
                status="chunked",
                error_msg=f"Embed failed: {str(e)[:200]}",
            )


async def _run_chunk(job_id: str, file_paths: list[str], folder_id: str | None, engine: RAGEngine, job_manager) -> None:
    """Run chunking (preview) in background."""
    import asyncio

    scanner = KnowledgeBaseScanner(engine.settings.knowledge_base, engine)
    callback = job_manager.make_progress_callback(job_id)

    job_manager.update_job(job_id, status="running", current_stage="scanning")
    try:
        result = await asyncio.to_thread(
            scanner.preview_chunks_sync, file_paths, folder_id, callback
        )
        if job_manager.is_cancelled(job_id):
            job_manager.update_job(job_id, status="cancelled", error="Cancelled by user")
        else:
            job_manager.update_job(
                job_id,
                status="completed",
                progress_pct=100,
                current_stage="completed",
            )
            job = job_manager.get_job(job_id)
            if job:
                job["result"] = {"results": result}
                job_manager._save()
    except Exception as e:
        logger.error("Chunk job %s failed: %s", job_id, e)
        job_manager.update_job(job_id, status="failed", error=str(e))


# ---------------------------------------------------------------------------
# Stats & Legacy Scan
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats(request: Request):
    engine: RAGEngine = get_engine(request)
    scanner = KnowledgeBaseScanner(engine.settings.knowledge_base, engine)
    return scanner.get_stats()


@router.post("/scan")
async def scan_kb(request: Request, admin=Depends(require_admin)):
    """Legacy one-step scan. Use preview-scan + preview-chunks + ingest for fine control."""
    engine: RAGEngine = get_engine(request)
    scanner = KnowledgeBaseScanner(engine.settings.knowledge_base, engine)
    result = await scanner.scan_all()
    return result.model_dump()


@router.get("/paths")
async def get_paths(request: Request):
    engine: RAGEngine = get_engine(request)
    cfg = engine.settings.knowledge_base
    return {
        "pdf_paths": cfg.pdf_paths,
        "markdown_paths": cfg.markdown_paths,
        "auto_scan": cfg.auto_scan,
        "scan_interval_hours": cfg.scan_interval_hours,
    }


@router.put("/paths")
async def update_paths(request: Request, body: PathsRequest, admin=Depends(require_admin)):
    engine: RAGEngine = get_engine(request)
    engine.settings.knowledge_base.pdf_paths = body.pdf_paths
    engine.settings.knowledge_base.markdown_paths = body.markdown_paths
    return {"success": True}


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

@router.get("/folders")
async def list_folders():
    return {"folders": registry.get_folder_tree()}


@router.post("/folders")
async def create_folder(body: FolderCreate, admin=Depends(require_admin)):
    folder = registry.create_folder(body.name, body.parent_id)
    return {"success": True, "folder": folder}


@router.put("/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderUpdate, admin=Depends(require_admin)):
    folder = registry.update_folder(folder_id, name=body.name, parent_id=body.parent_id)
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return {"success": True, "folder": folder}


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, admin=Depends(require_admin)):
    if folder_id == "root":
        raise HTTPException(status_code=400, detail="不能删除根目录")
    success = registry.delete_folder(folder_id)
    if not success:
        raise HTTPException(status_code=400, detail="文件夹非空或不存在，无法删除")
    return {"success": True}


# ---------------------------------------------------------------------------
# Files (Documents in KB)
# ---------------------------------------------------------------------------

@router.get("/files")
async def list_files(folder_id: str | None = None, status: str | None = None):
    docs = registry.get_documents(folder_id=folder_id, status=status)
    return {"files": docs}


@router.put("/files/{doc_id}/folder")
async def move_file(doc_id: str, body: dict, admin=Depends(require_admin)):
    folder_id = body.get("folder_id")
    doc = registry.update_document(doc_id, folder_id=folder_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"success": True}


@router.delete("/files/{doc_id}")
async def delete_file(doc_id: str, request: Request, admin=Depends(require_admin)):
    engine: RAGEngine = get_engine(request)
    engine.delete_document(doc_id)
    registry.delete_document(doc_id)
    return {"success": True}


# ---------------------------------------------------------------------------
# Step-by-step ingestion
# ---------------------------------------------------------------------------

@router.post("/preview-scan")
async def preview_scan(request: Request, admin=Depends(require_admin)):
    """Step 1: Preview scan - detect new/modified/unchanged/missing files."""
    engine: RAGEngine = get_engine(request)
    scanner = KnowledgeBaseScanner(engine.settings.knowledge_base, engine)
    results = await scanner.preview_scan()
    return {
        "success": True,
        "total": len(results),
        "new": [r for r in results if r["status"] == "new"],
        "modified": [r for r in results if r["status"] == "modified"],
        "unchanged": [r for r in results if r["status"] == "unchanged"],
        "missing": [r for r in results if r["status"] == "missing"],
    }


@router.post("/preview-chunks")
async def preview_chunks(request: Request, body: PreviewChunksRequest, admin=Depends(require_admin)):
    """Step 2: Generate chunk previews for selected files."""
    engine: RAGEngine = get_engine(request)
    scanner = KnowledgeBaseScanner(engine.settings.knowledge_base, engine)
    results = await scanner.preview_chunks(body.file_paths, folder_id=body.folder_id)
    return {"success": True, "results": results}


@router.post("/ingest")
async def ingest_documents(request: Request, body: IngestRequest, admin=Depends(require_admin)):
    """Step 3: Embed and index selected documents (background job)."""
    job_manager = request.app.state.job_manager
    engine: RAGEngine = get_engine(request)

    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="没有选择任何文档")

    job_id = job_manager.create_job(body.doc_ids, job_type="embed")
    asyncio.create_task(_run_embed(job_id, body.doc_ids, engine, job_manager))

    return {"success": True, "job_id": job_id}


@router.post("/chunk-jobs")
async def chunk_documents(request: Request, body: PreviewChunksRequest, admin=Depends(require_admin)):
    """Parse and chunk selected files (background job)."""
    job_manager = request.app.state.job_manager
    engine: RAGEngine = get_engine(request)

    if not body.file_paths:
        raise HTTPException(status_code=400, detail="没有选择任何文件")

    # Build doc_ids from file_paths for registry tracking
    job_id = job_manager.create_job(body.file_paths, job_type="chunk")
    asyncio.create_task(_run_chunk(job_id, body.file_paths, body.folder_id, engine, job_manager))

    return {"success": True, "job_id": job_id}


@router.post("/rechunk")
async def rechunk_documents(request: Request, body: IngestRequest, admin=Depends(require_admin)):
    """Re-parse and re-index selected documents with current chunking config (background job)."""
    job_manager = request.app.state.job_manager
    engine: RAGEngine = get_engine(request)

    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="没有选择任何文档")

    job_id = job_manager.create_job(body.doc_ids, job_type="embed")
    asyncio.create_task(_run_embed(job_id, body.doc_ids, engine, job_manager))

    return {"success": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Job progress endpoints
# ---------------------------------------------------------------------------

@router.get("/ingest/jobs")
async def list_ingest_jobs(request: Request, admin=Depends(require_admin), limit: int = 20):
    """List recent ingest jobs."""
    job_manager = request.app.state.job_manager
    jobs = job_manager.list_jobs(limit=limit)
    return {"success": True, "jobs": jobs}


@router.get("/ingest/jobs/{job_id}")
async def get_ingest_job(request: Request, job_id: str, admin=Depends(require_admin)):
    """Get status of a specific ingest job."""
    job_manager = request.app.state.job_manager
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "job": job}


@router.post("/ingest/jobs/{job_id}/cancel")
async def cancel_ingest_job(request: Request, job_id: str, admin=Depends(require_admin)):
    """Cancel a running or pending ingest job."""
    job_manager = request.app.state.job_manager
    ok = job_manager.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务不存在或已结束，无法取消")
    return {"success": True, "job_id": job_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Batch actions
# ---------------------------------------------------------------------------

@router.post("/batch-action")
async def batch_action(request: Request, body: BatchActionRequest, admin=Depends(require_admin)):
    """Batch delete, move, or rechunk documents."""
    engine: RAGEngine = get_engine(request)
    errors = []

    if body.action == "delete":
        for doc_id in body.doc_ids:
            try:
                engine.delete_document(doc_id)
                registry.delete_document(doc_id)
            except Exception as e:
                errors.append(f"{doc_id}: {e}")
        return {"success": True, "deleted": len(body.doc_ids), "errors": errors}

    elif body.action == "move":
        if not body.folder_id:
            raise HTTPException(status_code=400, detail="移动操作需要提供 folder_id")
        for doc_id in body.doc_ids:
            doc = registry.update_document(doc_id, folder_id=body.folder_id)
            if not doc:
                errors.append(f"{doc_id}: not found")
        return {"success": True, "moved": len(body.doc_ids), "errors": errors}

    elif body.action == "rechunk":
        job_manager = request.app.state.job_manager
        job_id = job_manager.create_job(body.doc_ids, job_type="embed")
        asyncio.create_task(_run_embed(job_id, body.doc_ids, engine, job_manager))
        return {"success": True, "job_id": job_id}

    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作: {body.action}")

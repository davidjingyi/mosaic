"""Document management API routes."""
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.api.v1.deps import get_engine, require_admin
from app.core.engine import RAGEngine
from app.models.schemas import UploadResponse
from app.services import document_registry as registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["文档管理"])
UPLOAD_DIR = Path("data/uploads")
SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".docx"}


def _validate_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTS


def _generate_doc_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    admin=Depends(require_admin),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")
    if not _validate_file(file.filename):
        raise HTTPException(status_code=400, detail="不支持的文件类型，仅支持 PDF/TXT/MD/DOCX")

    doc_id = _generate_doc_id()
    upload_subdir = UPLOAD_DIR / doc_id
    upload_subdir.mkdir(parents=True, exist_ok=True)
    file_path = upload_subdir / file.filename

    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        engine: RAGEngine = get_engine(request)
        doc_info = await engine.ingest_document(str(file_path), doc_id)
        # Register in unified registry
        registry.register_document(
            doc_id=doc_id,
            file_path=str(file_path),
            source="kb",
            folder_id="root",
            file_hash="",
            file_size=doc_info.file_size,
            status="indexed",
        )
        registry.update_document(doc_id, chunk_count=doc_info.chunk_count)
        return UploadResponse(
            doc_id=doc_id,
            filename=file.filename,
            status="success",
            message=f"文档上传成功，已分块为 {doc_info.chunk_count} 个片段",
        )
    except HTTPException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Document processing failed: %s", e)
        try:
            if upload_subdir.exists():
                shutil.rmtree(upload_subdir)
        except Exception as cleanup_err:
            logger.warning("Cleanup failed: %s", cleanup_err)
        raise HTTPException(status_code=500, detail=f"文档处理失败: {e}")


@router.get("")
async def list_documents(request: Request):
    engine: RAGEngine = get_engine(request)
    docs = engine.get_all_documents()
    return [doc.model_dump() for doc in docs]


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, request: Request, admin=Depends(require_admin)):
    engine: RAGEngine = get_engine(request)
    success = engine.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    upload_subdir = UPLOAD_DIR / doc_id
    if upload_subdir.exists():
        shutil.rmtree(upload_subdir)
    return {"success": True, "message": f"文档 {doc_id} 已删除"}

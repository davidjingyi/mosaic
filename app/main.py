"""FastAPI application entry point."""
import logging
import os

# Prevent HuggingFace/Transformers from making network requests during model loading.
# This avoids "client has been closed" errors when the network is unstable.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from pathlib import Path

print("[DEBUG] main.py loaded from", Path(__file__).absolute())

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import auth, cache, chat, chunks, config, documents, knowledge_base, model_configs, models, pdf_mapping, prompts, retrieval, sessions, stats
from app.api.v1.deps import require_admin
from app.dependencies import lifespan

__version__ = "2.0.0"

logger = logging.getLogger(__name__)

# CORS — default to localhost only for safety; production MUST set CORS_ORIGINS env var
_CORS_ENV = os.environ.get("CORS_ORIGINS", "")
if _CORS_ENV and _CORS_ENV != "*":
    CORS_ORIGINS = [o.strip() for o in _CORS_ENV.split(",") if o.strip()]
elif _CORS_ENV == "*":
    logger.warning("CORS_ORIGINS=* allows all origins — only use in development!")
    CORS_ORIGINS = ["*"]
else:
    CORS_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
CORS_CREDENTIALS = os.environ.get("CORS_CREDENTIALS", "false").lower() == "true"

# Static files
STATIC_DIR = Path(__file__).parent / "static"
ADMIN_DIR = STATIC_DIR / "admin"
HAS_FRONTEND = (STATIC_DIR / "index.html").exists()
HAS_ADMIN = (ADMIN_DIR / "index.html").exists()

# Upload limit (MB -> bytes)
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "50")) * 1024 * 1024

# API docs URLs
docs_url = "/api/docs" if HAS_FRONTEND else "/docs"
redoc_url = "/api/redoc" if HAS_FRONTEND else "/redoc"
openapi_url = "/api/openapi.json" if HAS_FRONTEND else "/openapi.json"

app = FastAPI(
    title="肿瘤患者智能答疑系统",
    description="基于RAG技术的肿瘤患者智能答疑系统",
    version=__version__,
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "服务器内部错误",
                "detail": str(exc) if os.environ.get("DEBUG") == "true" else None,
            }
        },
    )


@app.middleware("http")
async def limit_upload_size(request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_UPLOAD_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"上传文件过大，最大允许 {MAX_UPLOAD_SIZE // 1024 // 1024}MB"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"},
                )
    return await call_next(request)


# Ensure data dirs exist
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
(PROJECT_ROOT / "data" / "uploads").mkdir(parents=True, exist_ok=True)
(PROJECT_ROOT / "data" / "medct").mkdir(parents=True, exist_ok=True)
(PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)

# Protected upload download endpoint (replaces open StaticFiles mount)
@app.get("/uploads/{doc_id}/{filename}")
async def download_upload(doc_id: str, filename: str, admin=Depends(require_admin)):
    file_path = PROJECT_ROOT / "data" / "uploads" / doc_id / filename
    # Security: prevent path traversal
    try:
        file_path.resolve().relative_to((PROJECT_ROOT / "data" / "uploads").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)

# API routes (v1)
app.include_router(chat.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(retrieval.router, prefix="/api/v1")
app.include_router(knowledge_base.router, prefix="/api/v1")
app.include_router(config.router, prefix="/api/v1")
app.include_router(models.router, prefix="/api/v1")
app.include_router(prompts.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(cache.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(chunks.router, prefix="/api/v1")
app.include_router(model_configs.router, prefix="/api/v1")
app.include_router(pdf_mapping.router, prefix="/api/v1")


@app.get("/health")
async def health_check(request: Request):
    from fastapi import Request
    checks = {"service": "onco-rag", "version": __version__}
    # 1. Vector store check
    try:
        engine = request.app.state.engine
        _ = engine.vector_store.count()
        checks["vector_store"] = "ok"
    except Exception as e:
        checks["vector_store"] = f"error: {e}"
    # 2. File system check
    try:
        _test_path = Path("data/.healthcheck")
        _test_path.write_text("ok")
        _test_path.unlink()
        checks["filesystem"] = "ok"
    except Exception as e:
        checks["filesystem"] = f"error: {e}"
    # 3. LLM API connectivity (lightweight probe — list models)
    try:
        client = await engine.generator._get_client()
        resp = await client.get("/models", timeout=5.0)
        checks["llm_api"] = "ok" if resp.status_code in (200, 404) else f"status: {resp.status_code}"
    except Exception as e:
        checks["llm_api"] = f"unreachable: {type(e).__name__}"

    # DEBUG: check what config.yaml the backend actually sees
    try:
        import yaml
        with open("data/config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        checks["config_device"] = cfg.get("embedding", {}).get("device", "NOT_FOUND")
        checks["config_path"] = str(Path("data/config.yaml").absolute())
    except Exception as e:
        checks["config_error"] = str(e)

    all_ok = all(v == "ok" for k, v in checks.items() if k not in ("service", "version", "config_device", "config_path", "config_error"))
    status_code = 200 if all_ok else 503
    return JSONResponse(
        content={"status": "healthy" if all_ok else "degraded", **checks},
        status_code=status_code,
    )


# Admin dashboard
if HAS_ADMIN:
    app.mount("/admin/assets", StaticFiles(directory=ADMIN_DIR / "assets"), name="admin-assets")

    @app.get("/admin")
    async def admin_page():
        return FileResponse(ADMIN_DIR / "index.html")

    @app.get("/admin/{full_path:path}")
    async def admin_routes(full_path: str):
        return FileResponse(ADMIN_DIR / "index.html")


# Patient frontend
if HAS_FRONTEND:
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="frontend-assets")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def serve_frontend_routes(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("uploads/") or full_path.startswith("admin"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/")
    async def root():
        return {"name": "肿瘤患者智能答疑系统", "version": __version__, "docs": docs_url}

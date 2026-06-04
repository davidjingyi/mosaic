"""Cache management API routes."""
from fastapi import APIRouter, Depends, Request

from app.api.v1.deps import get_engine, require_admin
from app.core.engine import RAGEngine
from app.models.schemas import CacheConfigUpdate, CacheInvalidateRequest

router = APIRouter(prefix="/cache", tags=["语义缓存"])


@router.get("/stats")
async def get_cache_stats(request: Request):
    engine: RAGEngine = get_engine(request)
    return engine.cache.get_stats()


@router.get("/entries")
async def list_cache_entries(request: Request, limit: int = 50):
    engine: RAGEngine = get_engine(request)
    cache = engine.cache
    # Default cache doesn't expose entries directly; return stats for now
    stats = cache.get_stats()
    return {"entries": [], "stats": stats}


@router.delete("/invalidate")
async def invalidate_cache(request: Request, body: CacheInvalidateRequest, admin=Depends(require_admin)):
    engine: RAGEngine = get_engine(request)
    removed = engine.cache.invalidate(pattern=body.pattern)
    return {"success": True, "removed": removed, "pattern": body.pattern}


@router.put("/config")
async def update_cache_config(request: Request, body: CacheConfigUpdate, admin=Depends(require_admin)):
    engine: RAGEngine = get_engine(request)
    cache = engine.cache
    if body.enabled is not None:
        cache.config.enabled = body.enabled
    if body.max_size is not None:
        cache.config.max_size = body.max_size
    if body.similarity_threshold is not None:
        cache.config.similarity_threshold = body.similarity_threshold
    if body.default_ttl is not None:
        cache.config.default_ttl = body.default_ttl
    return {"success": True, "config": cache.config.model_dump()}

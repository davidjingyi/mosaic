"""API v1 routers."""
from fastapi import APIRouter

from app.api.v1 import cache, chat, config, documents, knowledge_base, model_configs, retrieval

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(retrieval.router)
api_router.include_router(knowledge_base.router)
api_router.include_router(config.router)
api_router.include_router(cache.router)
api_router.include_router(model_configs.router)

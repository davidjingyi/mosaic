"""Model listing API routes."""
import logging
import os

import httpx

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["模型管理"])

DEFAULT_LLM = [
    "qwen2.5", "qwen2.5:14b", "qwen2.5:7b", "llama3.2",
    "mistral", "deepseek-r1", "gemma2:9b",
]
EMBEDDING_MODELS = [
    "BAAI/bge-large-zh-v1.5", "BAAI/bge-small-zh-v1.5",
    "BAAI/bge-base-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-m3",
]
RERANK_MODELS = [
    "BAAI/bge-reranker-large", "BAAI/bge-reranker-base",
    "BAAI/bge-reranker-v2-m3",
]

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


# Reusable client for Ollama probing
_ollama_client: httpx.AsyncClient | None = None

async def _get_ollama_models() -> list[str]:
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = httpx.AsyncClient(timeout=5.0)
    try:
        response = await _ollama_client.get(f"{OLLAMA_URL}/api/tags")
        if response.status_code == 200:
            return [m["name"] for m in response.json().get("models", [])]
    except Exception as e:
        logger.debug("Ollama not reachable: %s", e)
    return []


@router.get("")
async def list_models():
    llm_models = await _get_ollama_models()
    if not llm_models:
        llm_models = DEFAULT_LLM
    return {
        "llm_models": llm_models,
        "embedding_models": EMBEDDING_MODELS,
        "rerank_models": RERANK_MODELS,
    }

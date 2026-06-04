"""Model configuration management API.

Supports multiple named LLM model configs with CRUD and activation.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.deps import get_manager, require_admin
from app.dependencies import ComponentManager
from app.services.model_store import ModelStore

router = APIRouter(prefix="/model-configs", tags=["模型配置管理"])


def _get_model_store(request: Request) -> ModelStore:
    """Get model store from app state."""
    return request.app.state.model_store


@router.get("")
async def list_models(request: Request):
    """List all saved model configs. Active model is first."""
    store = _get_model_store(request)
    return {"models": store.list_models(mask_key=True), "active_id": store._active_id}


@router.get("/{model_id}")
async def get_model(request: Request, model_id: str):
    """Get a single model config by ID."""
    store = _get_model_store(request)
    model = store.get(model_id, mask_key=True)
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return model


@router.post("")
async def create_model(
    request: Request,
    body: dict[str, Any],
    admin=Depends(require_admin),
):
    """Create a new named model config."""
    store = _get_model_store(request)
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="模型名称不能为空")

    config = body.get("config", {})
    model = store.create(name, config)
    return {"success": True, "model": model}


@router.put("/{model_id}")
async def update_model(
    request: Request,
    model_id: str,
    body: dict[str, Any],
    admin=Depends(require_admin),
):
    """Update an existing model config."""
    store = _get_model_store(request)
    config = body.get("config", {})
    # Allow name update through config
    if "name" in body:
        config["name"] = body["name"]

    model = store.update(model_id, config)
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return {"success": True, "model": model}


@router.post("/{model_id}/activate")
async def activate_model(
    request: Request,
    model_id: str,
    admin=Depends(require_admin),
):
    """Activate a model config (apply to current LLM settings)."""
    store = _get_model_store(request)
    if not store.set_active(model_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # Hot-reload generator with new config
    manager: ComponentManager = get_manager(request)
    active_config = store.get_active_llm_config()
    if active_config:
        from app.config import LLMConfig, Settings
        current = manager.settings.model_dump()
        current["llm"] = {**current.get("llm", {}), **active_config}
        new_settings = Settings(**current)
        await manager.reload_config(new_settings)

    return {"success": True, "active_id": model_id}


@router.delete("/{model_id}")
async def delete_model(
    request: Request,
    model_id: str,
    admin=Depends(require_admin),
):
    """Delete a model config."""
    store = _get_model_store(request)
    if not store.delete(model_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return {"success": True, "message": "模型配置已删除"}

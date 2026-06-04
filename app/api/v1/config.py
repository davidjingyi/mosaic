"""Configuration management API with hot-reload support."""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.deps import get_manager, get_settings, require_admin
from app.config import Settings
from app.core.config_profiles import get_profile_config, list_profiles, validate_custom_profile
from app.dependencies import ComponentManager
from app.models.schemas import ConfigUpdateRequest

router = APIRouter(prefix="/config", tags=["配置管理"])


def _mask_api_key(config_dict: dict) -> dict:
    llm = config_dict.get("llm", {})
    key = llm.get("api_key", "")
    if key and len(key) > 12:
        llm["api_key"] = f"{key[:8]}...{key[-4:]}"
    elif key:
        llm["api_key"] = "***"
    return config_dict


_config_yaml_path = Path("data/config.yaml")
_last_config_mtime: float = 0.0
_cached_settings_dict: dict | None = None


@router.get("")
async def get_config(request: Request):
    # Use memory-cached settings with mtime check to avoid repeated YAML parsing
    global _last_config_mtime, _cached_settings_dict
    try:
        current_mtime = _config_yaml_path.stat().st_mtime
    except OSError:
        current_mtime = 0.0

    if _cached_settings_dict is None or current_mtime > _last_config_mtime:
        settings = Settings.from_yaml(_config_yaml_path)
        _cached_settings_dict = settings.model_dump()
        _last_config_mtime = current_mtime

    return _mask_api_key(_cached_settings_dict)


@router.put("")
async def update_config(request: Request, body: ConfigUpdateRequest, admin=Depends(require_admin)):
    manager: ComponentManager = get_manager(request)
    try:
        current = manager.settings.model_dump()
        # Deep merge body.data into current
        _deep_merge(current, body.data)
        new_settings = Settings(**current)
        await manager.reload_config(new_settings)
        return {"success": True, "config": _mask_api_key(new_settings.model_dump())}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置更新失败: {e}")


@router.post("/reset")
async def reset_config(request: Request, admin=Depends(require_admin)):
    from app.config import Settings as SettingsCls
    manager: ComponentManager = get_manager(request)
    try:
        default = SettingsCls()
        manager.reload_config(default)
        return {"success": True, "config": _mask_api_key(default.model_dump())}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置重置失败: {e}")


@router.get("/profiles")
async def get_profiles():
    """List available preset configuration profiles."""
    return {"profiles": list_profiles()}


@router.post("/profiles/{profile_id}/apply")
async def apply_profile(
    request: Request,
    profile_id: str,
    admin=Depends(require_admin),
):
    """Apply a preset configuration profile."""
    manager: ComponentManager = get_manager(request)
    profile_config = get_profile_config(profile_id)

    if profile_config is None:
        raise HTTPException(status_code=404, detail=f"未知方案: {profile_id}")

    try:
        current = manager.settings.model_dump()
        _deep_merge(current, profile_config)
        new_settings = Settings(**current)
        await manager.reload_config(new_settings)
        return {
            "success": True,
            "profile_id": profile_id,
            "config": _mask_api_key(new_settings.model_dump()),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"应用方案失败: {e}")


@router.post("/upload-profile")
async def upload_profile(
    request: Request,
    body: dict[str, Any],
    admin=Depends(require_admin),
):
    """Upload and apply a custom configuration profile document."""
    manager: ComponentManager = get_manager(request)

    is_valid, error_msg = validate_custom_profile(body)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    try:
        current = manager.settings.model_dump()
        _deep_merge(current, body["config"])
        new_settings = Settings(**current)
        await manager.reload_config(new_settings)
        return {
            "success": True,
            "profile_name": body.get("name", ""),
            "description": body.get("description", ""),
            "config": _mask_api_key(new_settings.model_dump()),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"应用自定义方案失败: {e}")


def _deep_merge(base: dict, override: dict, depth: int = 0) -> None:
    if depth > 10:
        raise ValueError("Config nesting too deep (max 10 levels)")
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value, depth + 1)
        else:
            base[key] = value

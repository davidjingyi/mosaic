"""Prompt version management with persistent JSON storage."""
import json
import logging
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPTS_PATH = Path("data/prompts.json")

# In-memory cache with mtime tracking
_prompts_cache: dict[str, Any] | None = None
_prompts_cache_mtime: float = 0.0


def _load() -> dict[str, Any]:
    global _prompts_cache, _prompts_cache_mtime
    try:
        mtime = PROMPTS_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0

    if _prompts_cache is not None and _prompts_cache_mtime >= mtime:
        return _prompts_cache

    if PROMPTS_PATH.exists():
        try:
            with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _prompts_cache = data
            _prompts_cache_mtime = mtime
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load prompts: %s", e)
    _prompts_cache = {"prompts": []}
    _prompts_cache_mtime = mtime
    return _prompts_cache


def _save(data: dict) -> None:
    global _prompts_cache, _prompts_cache_mtime
    PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROMPTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _prompts_cache = data
    try:
        _prompts_cache_mtime = PROMPTS_PATH.stat().st_mtime
    except OSError:
        _prompts_cache_mtime = time.time()


def get_prompts() -> list[dict]:
    return _load().get("prompts", [])


def get_prompt(prompt_id: str) -> dict | None:
    for p in get_prompts():
        if p.get("id") == prompt_id:
            return p
    return None


def get_default_prompt() -> dict | None:
    prompts = get_prompts()
    for p in prompts:
        if p.get("is_default"):
            return p
    # Fallback to first prompt
    return prompts[0] if prompts else None


def create_prompt(name: str, content: str, is_default: bool = False) -> dict:
    data = _load()
    prompt = {
        "id": f"prompt-{uuid.uuid4().hex[:8]}",
        "name": name,
        "content": content,
        "is_default": is_default,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if is_default:
        for p in data["prompts"]:
            p["is_default"] = False
    data["prompts"].append(prompt)
    _save(data)
    return prompt


def update_prompt(prompt_id: str, **kwargs) -> dict | None:
    data = _load()
    for p in data["prompts"]:
        if p["id"] == prompt_id:
            for key in ("name", "content", "is_default"):
                if key in kwargs:
                    p[key] = kwargs[key]
            if kwargs.get("is_default"):
                for other in data["prompts"]:
                    if other["id"] != prompt_id:
                        other["is_default"] = False
            p["updated_at"] = _now()
            _save(data)
            return p
    return None


def delete_prompt(prompt_id: str) -> bool:
    data = _load()
    original = len(data["prompts"])
    data["prompts"] = [p for p in data["prompts"] if p["id"] != prompt_id]
    if len(data["prompts"]) < original:
        _save(data)
        return True
    return False


def import_from_config(system_prompt: str) -> dict:
    """Import existing system prompt from config as the first version."""
    data = _load()
    if not data["prompts"]:
        prompt = {
            "id": "default",
            "name": "默认肿瘤助手",
            "content": system_prompt,
            "is_default": True,
            "created_at": _now(),
            "updated_at": _now(),
        }
        data["prompts"].append(prompt)
        _save(data)
        return prompt
    return get_default_prompt()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

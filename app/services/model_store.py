"""LLM model configuration store with multiple model support.

Supports:
- Create/save multiple named model configs (base_url, api_key, model_name, etc.)
- Select one as active
- Delete saved models
- Persist to JSON file
"""
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """A single LLM model configuration."""
    model_id: str
    name: str
    base_url: str = ""
    api_key: str = ""
    api_id: str = ""
    model_name: str = ""
    temperature: float = 0.5
    max_tokens: int = 4096
    top_p: float = 0.9
    system_prompt: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self, mask_key: bool = True) -> dict[str, Any]:
        data = {
            "model_id": self.model_id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": self._mask_api_key(self.api_key) if mask_key else self.api_key,
            "api_id": self.api_id,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "system_prompt": self.system_prompt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        return data

    @staticmethod
    def _mask_api_key(key: str) -> str:
        if not key or len(key) <= 12:
            return "***" if key else ""
        return f"{key[:8]}...{key[-4:]}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelConfig":
        return cls(
            model_id=data.get("model_id", str(uuid.uuid4())),
            name=data.get("name", "未命名"),
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            api_id=data.get("api_id", ""),
            model_name=data.get("model_name", ""),
            temperature=data.get("temperature", 0.5),
            max_tokens=data.get("max_tokens", 4096),
            top_p=data.get("top_p", 0.9),
            system_prompt=data.get("system_prompt", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


class ModelStore:
    """JSON-based model configuration store."""

    def __init__(self, data_dir: Path, save_interval: int = 10):
        self.data_dir = data_dir
        self.store_path = data_dir / "model_configs.json"
        self._models: dict[str, ModelConfig] = {}
        self._active_id: str | None = None
        self._lock = threading.RLock()
        self._save_interval = save_interval
        self._last_save = 0.0
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("models", []):
                model = ModelConfig.from_dict(item)
                self._models[model.model_id] = model
            self._active_id = data.get("active_id")
            logger.info("Loaded %d model configs, active=%s", len(self._models), self._active_id)
        except Exception as e:
            logger.warning("Failed to load model configs: %s", e)

    def _save(self) -> None:
        try:
            with self._lock:
                data = {
                    "models": [m.to_dict(mask_key=False) for m in self._models.values()],
                    "active_id": self._active_id,
                }
            tmp = self.store_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                tmp.replace(self.store_path)
            except PermissionError:
                import os
                os.replace(str(tmp), str(self.store_path))
            self._last_save = time.time()
        except Exception as e:
            logger.warning("Failed to save model configs: %s", e)

    def _maybe_save(self) -> None:
        if time.time() - self._last_save >= self._save_interval:
            self._save()

    def list_models(self, mask_key: bool = True) -> list[dict[str, Any]]:
        """Return all model configs, active first."""
        with self._lock:
            models = sorted(
                self._models.values(),
                key=lambda m: (m.model_id != self._active_id, m.updated_at),
                reverse=True,
            )
            return [m.to_dict(mask_key=mask_key) for m in models]

    def get(self, model_id: str, mask_key: bool = True) -> dict[str, Any] | None:
        with self._lock:
            model = self._models.get(model_id)
            if model:
                return model.to_dict(mask_key=mask_key)
            return None

    def get_active(self, mask_key: bool = True) -> dict[str, Any] | None:
        with self._lock:
            if self._active_id and self._active_id in self._models:
                return self._models[self._active_id].to_dict(mask_key=mask_key)
            return None

    def get_active_llm_config(self) -> dict[str, Any] | None:
        """Return active model config formatted for LLMConfig."""
        with self._lock:
            if self._active_id and self._active_id in self._models:
                m = self._models[self._active_id]
                return {
                    "base_url": m.base_url,
                    "api_key": m.api_key,
                    "api_id": m.api_id,
                    "model_name": m.model_name,
                    "temperature": m.temperature,
                    "max_tokens": m.max_tokens,
                    "top_p": m.top_p,
                    "system_prompt": m.system_prompt,
                }
            return None

    def create(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new model config."""
        with self._lock:
            model_id = str(uuid.uuid4())
            model = ModelConfig(
                model_id=model_id,
                name=name,
                base_url=config.get("base_url", ""),
                api_key=config.get("api_key", ""),
                api_id=config.get("api_id", ""),
                model_name=config.get("model_name", ""),
                temperature=config.get("temperature", 0.5),
                max_tokens=config.get("max_tokens", 4096),
                top_p=config.get("top_p", 0.9),
                system_prompt=config.get("system_prompt", ""),
            )
            self._models[model_id] = model
            # Auto-activate if first model
            if len(self._models) == 1:
                self._active_id = model_id
            self._save()
            return model.to_dict(mask_key=True)

    def update(self, model_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing model config."""
        with self._lock:
            model = self._models.get(model_id)
            if not model:
                return None
            model.name = config.get("name", model.name)
            model.base_url = config.get("base_url", model.base_url)
            # Only update api_key if provided (not empty/masked)
            new_key = config.get("api_key", "")
            if new_key and "..." not in new_key:
                model.api_key = new_key
            model.api_id = config.get("api_id", model.api_id)
            model.model_name = config.get("model_name", model.model_name)
            model.temperature = config.get("temperature", model.temperature)
            model.max_tokens = config.get("max_tokens", model.max_tokens)
            model.top_p = config.get("top_p", model.top_p)
            model.system_prompt = config.get("system_prompt", model.system_prompt)
            model.updated_at = time.time()
            self._save()
            return model.to_dict(mask_key=True)

    def set_active(self, model_id: str) -> bool:
        """Set a model as the active one."""
        with self._lock:
            if model_id not in self._models:
                return False
            self._active_id = model_id
            self._save()
            return True

    def delete(self, model_id: str) -> bool:
        """Delete a model config."""
        with self._lock:
            if model_id not in self._models:
                return False
            del self._models[model_id]
            if self._active_id == model_id:
                # Auto-select another model as active
                self._active_id = next(iter(self._models.keys()), None)
            self._save()
            return True

    def apply_active_to_settings(self, settings_dict: dict[str, Any]) -> dict[str, Any]:
        """Merge active model config into settings dict."""
        active = self.get_active_llm_config()
        if active:
            settings_dict["llm"] = {**settings_dict.get("llm", {}), **active}
        return settings_dict

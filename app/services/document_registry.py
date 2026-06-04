"""Unified document registry with folder management and status tracking."""
import copy
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path("data/document_registry.json")
FOLDERS_PATH = Path("data/kb_folders.json")

SUPPORTED_STATUSES = {"pending", "scanned", "chunked", "embedding", "indexed", "failed"}

# In-memory cache for JSON files (single-process only; safe for typical deployment)
_json_cache: dict[str, Any] = {}
_json_cache_mtime: dict[str, float] = {}
_json_cache_lock = threading.Lock()
_file_locks: dict[str, FileLock] = {}

# Dedicated in-memory singleton for document registry to avoid stale-reference bugs
# when background threads call update_document() while API handlers call get_documents()
_registry_lock = threading.RLock()
_registry_data: dict[str, Any] | None = None
_registry_mtime: float = 0.0


def _deep_copy(data: Any) -> Any:
    """Deep copy registry data to prevent callers from mutating cached references."""
    return copy.deepcopy(data)


def _get_file_lock(path: Path) -> FileLock:
    key = str(path)
    if key not in _file_locks:
        _file_locks[key] = FileLock(str(path) + ".lock")
    return _file_locks[key]


def _ensure_paths():
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any, use_cache: bool = True) -> Any:
    cache_key = str(path)
    if use_cache and path.exists():
        mtime = os.path.getmtime(path)
        with _json_cache_lock:
            if cache_key in _json_cache and _json_cache_mtime.get(cache_key, 0) >= mtime:
                return _json_cache[cache_key]

    lock = _get_file_lock(path)
    with lock:
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            mtime = os.path.getmtime(path) if path.exists() else time.time()
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load %s: %s", path, e)
            return default

    with _json_cache_lock:
        _json_cache[cache_key] = data
        _json_cache_mtime[cache_key] = mtime
    return data


def _load_registry_from_disk() -> dict[str, Any]:
    """Always read the latest document_registry.json from disk."""
    lock = _get_file_lock(REGISTRY_PATH)
    with lock:
        if not REGISTRY_PATH.exists():
            return {"documents": {}, "version": "2.1"}
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load registry: %s", e)
            return {"documents": {}, "version": "2.1"}


def _save_json(path: Path, data: Any) -> None:
    lock = _get_file_lock(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(path)
            mtime = os.path.getmtime(path) if path.exists() else time.time()
        except OSError as e:
            logger.error("Failed to save %s: %s", path, e)
            return

    with _json_cache_lock:
        _json_cache[str(path)] = data
        _json_cache_mtime[str(path)] = mtime


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def get_folders() -> list[dict[str, Any]]:
    data = _load_json(FOLDERS_PATH, {"folders": []})
    return data.get("folders", [])


def get_folder_tree() -> list[dict[str, Any]]:
    """Return folders as a nested tree structure."""
    folders = get_folders()
    if not folders:
        # Auto-create root
        root = {"id": "root", "name": "根目录", "parent_id": None, "path": "/", "created_at": _now()}
        _save_json(FOLDERS_PATH, {"folders": [root]})
        return [root]

    folder_map = {f["id"]: {**f, "children": []} for f in folders}
    tree = []
    for f in folders:
        pid = f.get("parent_id")
        if pid and pid in folder_map:
            folder_map[pid]["children"].append(folder_map[f["id"]])
        elif pid is None:
            tree.append(folder_map[f["id"]])
    return tree


def create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
    folders = get_folders()
    folder_id = f"folder-{uuid.uuid4().hex[:8]}"
    # Build path
    path = f"/{name}"
    if parent_id:
        parent = next((f for f in folders if f["id"] == parent_id), None)
        if parent:
            path = f"{parent.get('path', '')}/{name}".replace("//", "/")
    folder = {
        "id": folder_id,
        "name": name,
        "parent_id": parent_id,
        "path": path,
        "created_at": _now(),
    }
    folders.append(folder)
    _save_json(FOLDERS_PATH, {"folders": folders})
    return folder


def update_folder(folder_id: str, name: str | None = None, parent_id: str | None = None) -> dict[str, Any] | None:
    folders = get_folders()
    for f in folders:
        if f["id"] == folder_id:
            if name:
                f["name"] = name
            if parent_id is not None:
                f["parent_id"] = parent_id
            # Rebuild paths
            _rebuild_paths(folders)
            _save_json(FOLDERS_PATH, {"folders": folders})
            return f
    return None


def delete_folder(folder_id: str) -> bool:
    folders = get_folders()
    # Cannot delete if has children
    children = [f for f in folders if f.get("parent_id") == folder_id]
    if children:
        return False
    # Cannot delete if has documents
    docs = get_documents(folder_id=folder_id)
    if docs:
        return False
    new_folders = [f for f in folders if f["id"] != folder_id]
    _save_json(FOLDERS_PATH, {"folders": new_folders})
    return True


def _rebuild_paths(folders: list[dict]) -> None:
    folder_map = {f["id"]: f for f in folders}
    for f in folders:
        parts = [f["name"]]
        current = f.get("parent_id")
        while current and current in folder_map:
            parts.insert(0, folder_map[current]["name"])
            current = folder_map[current].get("parent_id")
        f["path"] = "/" + "/".join(parts)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def get_registry() -> dict[str, Any]:
    """Return a DEEP COPY of the registry to prevent stale-reference bugs.

    Each caller gets its own isolated copy, so background threads and API
    handlers cannot accidentally mutate each other's data.
    """
    global _registry_data, _registry_mtime
    with _registry_lock:
        # Always check mtime to detect external modifications (e.g. by background threads)
        current_mtime = os.path.getmtime(REGISTRY_PATH) if REGISTRY_PATH.exists() else 0
        if _registry_data is None or _registry_mtime < current_mtime:
            _registry_data = _load_registry_from_disk()
            _registry_mtime = current_mtime
            # Auto-migrate from legacy kb_index.json if registry is empty
            if not _registry_data.get("documents"):
                legacy_path = Path("data/kb_index.json")
                if legacy_path.exists():
                    try:
                        legacy = _load_json(legacy_path, {})
                        for doc_id, doc in legacy.items():
                            _registry_data["documents"][doc_id] = {
                                "doc_id": doc_id,
                                "file_path": doc.get("file_path", ""),
                                "source": "kb",
                                "folder_id": "root",
                                "file_hash": doc.get("file_hash", ""),
                                "file_size": doc.get("file_size", 0),
                                "chunk_count": doc.get("chunk_count", 0),
                                "chunk_ids": [],
                                "status": doc.get("status", "indexed"),
                                "metadata": {},
                                "created_at": doc.get("imported_at", _now()),
                                "updated_at": _now(),
                                "error_msg": "",
                                "scan_info": {},
                                "chunk_preview": [],
                            }
                        if _registry_data["documents"]:
                            _save_json(REGISTRY_PATH, _registry_data)
                            _registry_mtime = os.path.getmtime(REGISTRY_PATH)
                            logger.info("Migrated %d documents from legacy kb_index.json", len(_registry_data["documents"]))
                    except Exception as e:
                        logger.warning("Legacy migration failed: %s", e)
        # Return deep copy so callers cannot mutate the cached singleton
        return _deep_copy(_registry_data)


def save_registry(registry: dict[str, Any]) -> None:
    """Save registry to disk and update in-memory cache atomically.

    The supplied registry is deep-copied before caching so that any
    subsequent caller mutation does not corrupt the cached singleton.
    """
    global _registry_data, _registry_mtime
    _ensure_paths()
    _save_json(REGISTRY_PATH, registry)
    with _registry_lock:
        _registry_data = _deep_copy(registry)
        _registry_mtime = os.path.getmtime(REGISTRY_PATH) if REGISTRY_PATH.exists() else time.time()


def get_document(doc_id: str) -> dict[str, Any] | None:
    reg = get_registry()
    return reg["documents"].get(doc_id)


def get_documents(folder_id: str | None = None, status: str | None = None, source: str | None = None) -> list[dict[str, Any]]:
    reg = get_registry()
    docs = list(reg["documents"].values())
    if folder_id:
        # "root" also matches unassigned (None) documents
        if folder_id == "root":
            docs = [d for d in docs if d.get("folder_id") in (folder_id, None)]
        else:
            docs = [d for d in docs if d.get("folder_id") == folder_id]
    if status:
        docs = [d for d in docs if d.get("status") == status]
    if source:
        docs = [d for d in docs if d.get("source") == source]
    return docs


def register_document(
    doc_id: str,
    file_path: str,
    source: str = "kb",
    folder_id: str | None = None,
    file_hash: str = "",
    file_size: int = 0,
    status: str = "pending",
) -> dict[str, Any]:
    if status not in SUPPORTED_STATUSES:
        status = "pending"
    with _registry_lock:
        reg = get_registry()
        doc = {
            "doc_id": doc_id,
            "file_path": file_path,
            "source": source,
            "folder_id": folder_id,
            "file_hash": file_hash,
            "file_size": file_size,
            "chunk_count": 0,
            "chunk_ids": [],
            "status": status,
            "metadata": {},
            "created_at": _now(),
            "updated_at": _now(),
            "error_msg": "",
            "scan_info": {},
            "chunk_preview": [],
        }
        reg["documents"][doc_id] = doc
        save_registry(reg)
        return doc


def update_document(doc_id: str, **kwargs) -> dict[str, Any] | None:
    """Atomically update a document in the registry.

    The entire read-modify-write cycle is protected by _registry_lock so
    that concurrent updates from background threads and API handlers do
    not overwrite each other.
    """
    with _registry_lock:
        reg = get_registry()
        doc = reg["documents"].get(doc_id)
        if not doc:
            return None
        for key, value in kwargs.items():
            if key in {"status", "chunk_count", "chunk_ids", "folder_id", "file_hash", "metadata", "error_msg", "scan_info", "chunk_preview"}:
                doc[key] = value
        doc["updated_at"] = _now()
        save_registry(reg)
        return doc


def delete_document(doc_id: str) -> bool:
    with _registry_lock:
        reg = get_registry()
        if doc_id in reg["documents"]:
            del reg["documents"][doc_id]
            save_registry(reg)
            return True
        return False


def get_stats() -> dict[str, Any]:
    reg = get_registry()
    docs = list(reg["documents"].values())
    total_size = sum(d.get("file_size", 0) for d in docs)
    status_counts = {}
    for s in SUPPORTED_STATUSES:
        status_counts[s] = sum(1 for d in docs if d.get("status") == s)
    return {
        "total_documents": len(docs),
        "total_chunks": sum(d.get("chunk_count", 0) for d in docs),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "status_counts": status_counts,
        "folders": get_folder_tree(),
    }


def _now() -> str:
    return datetime.now().isoformat()

"""Session history persistence for chat conversations."""
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
class Session:
    session_id: str
    title: str
    username: str = ""  # 会话所属用户，空字符串表示未绑定
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content, "time": time.time()})
        self.updated_at = time.time()
        # Auto-title from first user message
        if self.title == "新会话" and role == "user":
            self.title = content[:20] + "..." if len(content) > 20 else content

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "username": self.username,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }


class SessionStore:
    """JSON-based session store with auto-save."""

    def __init__(self, data_dir: Path, save_interval: int = 30):
        self.data_dir = data_dir
        self.store_path = data_dir / "sessions.json"
        self._sessions: dict[str, Session] = {}
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
            for item in data.get("sessions", []):
                sess = Session(
                    session_id=item["session_id"],
                    title=item["title"],
                    username=item.get("username", ""),
                    created_at=item.get("created_at", time.time()),
                    updated_at=item.get("updated_at", time.time()),
                    messages=item.get("messages", []),
                )
                self._sessions[sess.session_id] = sess
            logger.info("Loaded %d sessions", len(self._sessions))
        except Exception as e:
            logger.warning("Failed to load sessions: %s", e)

    def _save(self) -> None:
        try:
            with self._lock:
                data = {"sessions": [s.to_dict() for s in self._sessions.values()]}
            tmp = self.store_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Atomic replacement: handle Windows PermissionError by trying os.replace
            try:
                tmp.replace(self.store_path)
            except PermissionError:
                import os
                os.replace(str(tmp), str(self.store_path))
            self._last_save = time.time()
        except Exception as e:
            logger.warning("Failed to save sessions: %s", e)

    def _maybe_save(self) -> None:
        if time.time() - self._last_save >= self._save_interval:
            self._save()

    def create(self, title: str = "新会话", username: str = "") -> Session:
        with self._lock:
            session_id = str(uuid.uuid4())  # full UUID for security
            sess = Session(session_id=session_id, title=title, username=username)
            self._sessions[session_id] = sess
            self._maybe_save()
            return sess

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list_all(self, limit: int = 100) -> list[Session]:
        with self._lock:
            sessions = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
            return sessions[:limit]

    def list_by_user(self, username: str, limit: int = 100) -> list[Session]:
        """列出指定用户的会话（背靠背隔离）"""
        with self._lock:
            user_sessions = [s for s in self._sessions.values() if s.username == username]
            user_sessions.sort(key=lambda s: s.updated_at, reverse=True)
            return user_sessions[:limit]

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._maybe_save()
                return True
            return False

    def update_messages(self, session_id: str, messages: list[dict[str, Any]]) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            sess.messages = messages
            sess.updated_at = time.time()
            self._maybe_save()
            return True

    def clear_all(self) -> int:
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._save()
            return count

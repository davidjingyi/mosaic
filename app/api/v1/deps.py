"""Common dependencies for API v1 routes."""
from fastapi import HTTPException, Request

from app.config import Settings
from app.core.auth import verify_token
from app.core.engine import RAGEngine
from app.dependencies import ComponentManager
from app.services.session_store import SessionStore


def get_engine(request: Request) -> RAGEngine:
    return request.app.state.engine


def get_manager(request: Request) -> ComponentManager:
    return request.app.state.manager


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_current_user(request: Request) -> dict:
    """Parse and verify Bearer token from Authorization header.

    Returns the JWT payload dict. Raises 401 if token is missing or invalid.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证令牌，请重新登录")
    token = auth_header[7:]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期，请重新登录")
    return payload


def require_admin(request: Request) -> dict:
    """Dependency to enforce admin authentication."""
    payload = get_current_user(request)
    return payload

"""Session management API for chat history."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import get_session_store, require_admin
from app.core.auth import verify_token
from app.core.user_auth import verify_user_token
from app.services.session_store import SessionStore

router = APIRouter(prefix="/sessions", tags=["会话管理"])


class CreateSessionRequest(BaseModel):
    title: str = Field(default="新会话")


class UpdateSessionRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list, max_length=200)


def _resolve_user(request: Request) -> tuple[str | None, bool]:
    """从 Authorization header 解析用户身份。
    
    Returns:
        (username, is_admin): 用户名和是否管理员。
        - (None, False): 未认证
        - ("admin", True): 管理员令牌
        - ("someuser", False): 普通用户令牌
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, False
    token = auth_header[7:]

    # 先试管理员令牌
    payload = verify_token(token)
    if payload:
        return payload.get("sub", "admin"), True

    # 再试用户令牌
    username = verify_user_token(token)
    if username:
        return username, False

    return None, False


@router.post("")
async def create_session(request: Request, body: CreateSessionRequest):
    store: SessionStore = get_session_store(request)
    username, _ = _resolve_user(request)
    sess = store.create(title=body.title, username=username or "")
    return sess.to_dict()


@router.get("")
async def list_sessions(request: Request, limit: int = 100):
    store: SessionStore = get_session_store(request)
    username, is_admin = _resolve_user(request)
    if is_admin:
        sessions = store.list_all(limit=limit)
    elif username:
        sessions = store.list_by_user(username, limit=limit)
    else:
        raise HTTPException(status_code=401, detail="请先登录")
    return [s.to_dict() for s in sessions]


def _verify_ownership(request: Request, store: SessionStore, session_id: str) -> tuple:
    """验证会话所有权。返回 (session, username, is_admin) 或抛出 403/404。"""
    username, is_admin = _resolve_user(request)
    sess = store.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not is_admin and sess.username and sess.username != username:
        raise HTTPException(status_code=403, detail="无权访问他人会话")
    return sess, username, is_admin


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    store: SessionStore = get_session_store(request)
    sess, _, _ = _verify_ownership(request, store, session_id)
    return sess.to_dict()


@router.put("/{session_id}")
async def update_session(session_id: str, request: Request, body: UpdateSessionRequest):
    store: SessionStore = get_session_store(request)
    _verify_ownership(request, store, session_id)  # 抛异常即拦截
    success = store.update_messages(session_id, body.messages)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"success": True}


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    store: SessionStore = get_session_store(request)
    _verify_ownership(request, store, session_id)  # 抛异常即拦截
    success = store.delete(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"success": True}


@router.delete("")
async def clear_all_sessions(request: Request, admin=Depends(require_admin)):
    store: SessionStore = get_session_store(request)
    count = store.clear_all()
    return {"success": True, "cleared": count}

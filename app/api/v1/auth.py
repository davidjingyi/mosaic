"""Authentication API routes."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import get_current_user
from app.core.auth import authenticate, change_password, create_access_token
from app.core.rate_limit import require_login_rate_limit
from app.core.user_auth import register_user, authenticate_user, create_user_token, verify_user_token, cleanup_stale_accounts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str = Field(...)
    password: str = Field(...)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(...)
    new_password: str = Field(..., min_length=6)


@router.post("/login")
async def login(request: Request, body: LoginRequest, _rate_limit=Depends(require_login_rate_limit)):
    if not authenticate(body.username, body.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token({"sub": body.username, "role": "admin"})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/password")
async def update_password(request: Request, body: PasswordChangeRequest, user=Depends(get_current_user)):
    if not change_password(body.old_password, body.new_password):
        raise HTTPException(status_code=401, detail="旧密码错误")
    return {"success": True, "message": "密码已修改"}


@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    return {"username": user.get("sub"), "role": user.get("role")}


# ── User-facing auth ──

class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=4, max_length=100)


class UserLoginRequest(BaseModel):
    username: str = Field(...)
    password: str = Field(...)


@router.post("/register")
async def user_register(body: UserRegisterRequest):
    """用户注册"""
    try:
        user = register_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token = create_user_token(user["username"])
    return {"access_token": token, "token_type": "bearer", "username": user["username"]}


@router.post("/user-login")
async def user_login(body: UserLoginRequest):
    """用户登录"""
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_user_token(user["username"])
    return {"access_token": token, "token_type": "bearer", "username": user["username"]}


@router.get("/user-me")
async def get_user_me(request: Request):
    """获取当前登录用户信息"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = auth_header[7:]
    username = verify_user_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return {"username": username, "role": "user"}


class ActivationCodeRequest(BaseModel):
    username: str = Field(...)
    password: str = Field(...)


@router.post("/activation-code")
async def get_activation_code(body: ActivationCodeRequest):
    """用户用密码获取付费激活码（需验证密码正确）"""
    from app.core.user_auth import authenticate_user
    from app.services.password_deriver import derive_activation_code

    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 如果用户是老用户（注册时未派生），则实时派生
    code = user.get("activation_code")
    if not code:
        code = derive_activation_code(body.password)

    return {"username": body.username, "activation_code": code}


# ── Admin user management ──

@router.get("/admin/users")
async def list_users(request: Request, admin=Depends(get_current_user)):
    """管理后台：获取所有注册用户列表及活动数据"""
    from app.core.user_auth import list_all_users
    users = list_all_users()
    return {"total": len(users), "users": users}


@router.get("/admin/users/{username}/activation-code")
async def get_user_activation_code(username: str, admin=Depends(get_current_user)):
    """管理后台：获取指定用户的付费激活码（无需用户密码）"""
    from app.core.user_auth import _load_users
    users = _load_users()
    user = users.get(username.lower())
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    code = user.get("activation_code")
    if not code:
        raise HTTPException(status_code=404, detail="该用户未派生激活码")

    return {"username": username, "activation_code": code}


@router.delete("/admin/users/{username}")
async def delete_user(username: str, admin=Depends(get_current_user)):
    """管理后台：删除指定用户"""
    from app.core.user_auth import delete_user as do_delete
    if not do_delete(username):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"success": True, "username": username}


@router.put("/admin/users/{username}/freeze")
async def freeze_user(username: str, admin=Depends(get_current_user)):
    """管理后台：冻结用户"""
    from app.core.user_auth import freeze_user as do_freeze
    if not do_freeze(username):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"success": True, "username": username, "frozen": True}


@router.put("/admin/users/{username}/unfreeze")
async def unfreeze_user(username: str, admin=Depends(get_current_user)):
    """管理后台：解冻用户"""
    from app.core.user_auth import unfreeze_user as do_unfreeze
    if not do_unfreeze(username):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"success": True, "username": username, "frozen": False}

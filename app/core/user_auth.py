"""User authentication: registration, login, JWT tokens, and cleanup."""
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

USER_AUTH_FILE = Path("data/user_auth.json")
_JWT_SECRET_KEY = secrets.token_hex(32)  # Users use a separate JWT secret
ALGORITHM = "HS256"
USER_TOKEN_EXPIRE_DAYS = 7

# Reuse admin JWT secret if set, otherwise auto-generate
import os
if os.environ.get("ADMIN_SECRET_KEY"):
    _JWT_SECRET_KEY = os.environ["ADMIN_SECRET_KEY"]


class UserAccount(BaseModel):
    username: str
    password_hash: str
    created_at: str
    last_login_at: str


def _load_users() -> dict[str, dict]:
    if USER_AUTH_FILE.exists():
        try:
            with open(USER_AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load user auth file: %s", e)
    return {}


def _save_users(data: dict[str, dict]) -> None:
    USER_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_pw(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def register_user(username: str, password: str) -> dict:
    """Register a new user. Returns user info dict or raises ValueError."""
    username = username.strip().lower()
    if not username or len(username) < 2:
        raise ValueError("用户名至少需要2个字符")
    if not password or len(password) < 4:
        raise ValueError("密码至少需要4个字符")
    
    users = _load_users()
    if username in users:
        raise ValueError("用户名已存在")
    
    now = datetime.now(timezone.utc).isoformat()
    # Derive activation code from raw password at registration time
    from app.services.password_deriver import derive_activation_code
    activation_code = derive_activation_code(password)
    
    users[username] = {
        "username": username,
        "password_hash": _hash_pw(password),
        "activation_code": activation_code,
        "frozen": False,
        "created_at": now,
        "last_login_at": now,
    }
    _save_users(users)
    logger.info("User registered: %s", username)
    return {"username": username, "created_at": now}


def authenticate_user(username: str, password: str) -> dict | None:
    """Verify user credentials. Returns user dict or None."""
    username = username.strip().lower()
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    if not _verify_pw(password, user["password_hash"]):
        return None
    # Reject frozen users
    if user.get("frozen"):
        return None
    
    # Update last login time
    user["last_login_at"] = datetime.now(timezone.utc).isoformat()
    _save_users(users)
    return {"username": user["username"], "created_at": user["created_at"]}


def create_user_token(username: str) -> str:
    """Create a JWT access token for a user."""
    expire = datetime.now(timezone.utc) + timedelta(days=USER_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": username,
        "role": "user",
        "exp": expire,
    }
    return jwt.encode(payload, _JWT_SECRET_KEY, algorithm=ALGORITHM)


def verify_user_token(token: str) -> str | None:
    """Verify a user JWT token. Returns username or None."""
    try:
        payload = jwt.decode(token, _JWT_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "user":
            return None
        username = payload.get("sub")
        # Check user still exists (not deleted) and not frozen
        users = _load_users()
        if username not in users:
            return None
        if users[username].get("frozen"):
            return None
        return username
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def record_user_activity(username: str) -> None:
    """Record the latest activity timestamp for a user."""
    username = username.strip().lower()
    users = _load_users()
    if username not in users:
        return
    now = datetime.now(timezone.utc).isoformat()
    users[username]["latest_activity_at"] = now
    # Track total message count
    users[username]["message_count"] = users[username].get("message_count", 0) + 1
    _save_users(users)


def list_all_users() -> list[dict]:
    """List all registered users with their stats."""
    users = _load_users()
    result = []
    for username, data in users.items():
        result.append({
            "username": data.get("username", username),
            "frozen": data.get("frozen", False),
            "created_at": data.get("created_at", ""),
            "last_login_at": data.get("last_login_at", ""),
            "latest_activity_at": data.get("latest_activity_at", data.get("last_login_at", "")),
            "message_count": data.get("message_count", 0),
        })
    # Sort by latest activity (most recent first)
    result.sort(key=lambda u: u["latest_activity_at"], reverse=True)
    return result


def freeze_user(username: str) -> bool:
    """Freeze a user account. Returns True if frozen, False if not found."""
    username = username.strip().lower()
    users = _load_users()
    if username not in users:
        return False
    users[username]["frozen"] = True
    _save_users(users)
    logger.info("User frozen: %s", username)
    return True


def unfreeze_user(username: str) -> bool:
    """Unfreeze a user account. Returns True if unfrozen, False if not found."""
    username = username.strip().lower()
    users = _load_users()
    if username not in users:
        return False
    users[username]["frozen"] = False
    _save_users(users)
    logger.info("User unfrozen: %s", username)
    return True


def delete_user(username: str) -> bool:
    """Delete a user by username. Returns True if deleted, False if not found."""
    username = username.strip().lower()
    users = _load_users()
    if username not in users:
        return False
    del users[username]
    _save_users(users)
    logger.info("User deleted: %s", username)
    return True


def cleanup_stale_accounts(days: int = 30) -> int:
    """Remove user accounts that haven't logged in for the given days.
    Returns number of removed accounts.
    """
    users = _load_users()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = []
    for username, data in users.items():
        try:
            last_login = datetime.fromisoformat(data.get("last_login_at", ""))
            if last_login < cutoff:
                stale.append(username)
        except (ValueError, TypeError):
            stale.append(username)
    
    for username in stale:
        del users[username]
    
    if stale:
        _save_users(users)
        logger.info("Cleaned up %d stale user accounts (inactive > %d days)", len(stale), days)
    
    return len(stale)

"""Authentication utilities: JWT + bcrypt password hashing."""

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt

logger = logging.getLogger(__name__)

AUTH_FILE = Path('data/admin_auth.json')
_DEFAULT_SECRET = 'onco-rag-default-secret-do-not-use-in-production'
SECRET_KEY = os.environ.get('ADMIN_SECRET_KEY', _DEFAULT_SECRET)

# Read from .env file if env var not set
if SECRET_KEY == _DEFAULT_SECRET:
    env_path = Path(__file__).parent.parent.parent.parent / '.env'
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('ADMIN_SECRET_KEY='):
                    SECRET_KEY = line.split('=', 1)[1].strip().strip('"\'')
                    break

if SECRET_KEY == _DEFAULT_SECRET or len(SECRET_KEY) < 32:
    SECRET_KEY = secrets.token_urlsafe(32)
    logger.warning('ADMIN_SECRET_KEY not set — using auto-generated key.')

ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_DAYS = 7

def _load_auth() -> dict:
    """Load admin authentication data from file."""
    if AUTH_FILE.exists():
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load auth file: %s", e)
    return {}


def _save_auth(data: dict) -> None:
    """Save admin authentication data to file."""
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def init_admin_auth() -> None:
    """Initialize admin account on first startup."""
    auth = _load_auth()
    if not auth.get("admin"):
        auth["admin"] = {
            "username": "admin",
            "password_hash": _hash_password("123456"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_auth(auth)
        logger.info("Admin account initialized with default password")


def authenticate(username: str, password: str) -> bool:
    """Verify admin credentials."""
    auth = _load_auth()
    admin = auth.get("admin")
    if not admin or admin.get("username") != username:
        return False
    return _verify_password(password, admin["password_hash"])


def change_password(old_password: str, new_password: str) -> bool:
    """Change admin password."""
    if not authenticate("admin", old_password):
        return False
    auth = _load_auth()
    auth["admin"]["password_hash"] = _hash_password(new_password)
    auth["admin"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_auth(auth)
    return True


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Verify and decode a JWT access token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

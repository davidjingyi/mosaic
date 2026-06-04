"""Simple in-memory rate limiter for API endpoints."""
import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class SimpleRateLimiter:
    """Token-bucket style rate limiter per client IP."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # ip -> list of timestamps
        self._records: dict[str, list[float]] = defaultdict(list)

    def _clean_old(self, ip: str) -> None:
        cutoff = time.time() - self.window_seconds
        self._records[ip] = [t for t in self._records[ip] if t > cutoff]

    def is_allowed(self, ip: str) -> bool:
        self._clean_old(ip)
        return len(self._records[ip]) < self.max_requests

    def record(self, ip: str) -> None:
        self._records[ip].append(time.time())


# Global limiters
_login_limiter = SimpleRateLimiter(max_requests=5, window_seconds=60)


def require_login_rate_limit(request: Request) -> None:
    """Dependency to enforce rate limiting on login endpoint."""
    # Try X-Forwarded-For first, then direct remote address
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    if not _login_limiter.is_allowed(client_ip):
        logger.warning("Rate limit exceeded for IP: %s", client_ip)
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    _login_limiter.record(client_ip)

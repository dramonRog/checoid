"""SlowAPI rate limiting helpers for auth / extract endpoints."""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.backend.core.config import settings


def _rate_limit_key(request) -> str:
    """Prefer authenticated user id when present; else client IP."""
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer ") and len(auth) > 20:
        # Token fingerprint keeps per-user buckets without decoding JWT here
        return f"token:{auth[7:25]}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=_rate_limit_key,
    enabled=settings.RATE_LIMIT_ENABLED,
    default_limits=[settings.RATE_LIMIT_DEFAULT] if settings.RATE_LIMIT_ENABLED else [],
)

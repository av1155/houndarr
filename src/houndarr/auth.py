"""Authentication: password hashing, session management, login middleware."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from hmac import compare_digest
from typing import Any

import bcrypt
from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from houndarr.database import get_setting, set_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SESSION_COOKIE_NAME = "houndarr_session"
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours
BCRYPT_COST = 12
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]+$")

# Routes that don't require authentication
_PUBLIC_PATHS = frozenset(
    [
        "/setup",
        "/login",
        "/api/health",
        "/static",
    ]
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt cost 12."""
    salt = bcrypt.gensalt(rounds=BCRYPT_COST)
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Return True if password matches the bcrypt hash."""
    try:
        return bool(bcrypt.checkpw(password.encode(), hashed.encode()))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session serializer (lazy-initialized from DB secret)
# ---------------------------------------------------------------------------

_serializer: URLSafeTimedSerializer | None = None


async def _get_serializer() -> URLSafeTimedSerializer:
    global _serializer  # noqa: PLW0603
    if _serializer is None:
        secret = await get_setting("session_secret")
        if not secret:
            secret = os.urandom(32).hex()
            await set_setting("session_secret", secret)
        _serializer = URLSafeTimedSerializer(secret, salt="session")
    return _serializer


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def create_session(response: Response) -> None:
    """Create a new session and set the cookie on response."""
    serializer = await _get_serializer()
    payload = {"ts": int(time.time())}
    token = serializer.dumps(payload)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # Users set HTTPS via reverse proxy; we don't force it
    )


async def validate_session(request: Request) -> bool:
    """Return True if the request has a valid, non-expired session."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    try:
        serializer = await _get_serializer()
        serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        return True
    except (SignatureExpired, BadSignature):
        return False


def clear_session(response: Response) -> None:
    """Delete the session cookie."""
    response.delete_cookie(SESSION_COOKIE_NAME)


# ---------------------------------------------------------------------------
# Setup state helpers
# ---------------------------------------------------------------------------


async def is_setup_complete() -> bool:
    """Return True if the initial password has been set."""
    return (await get_setting("password_hash")) is not None


async def set_password(password: str) -> None:
    """Hash and persist the application password."""
    await set_setting("password_hash", hash_password(password))


def normalize_username(username: str) -> str:
    """Return a normalized username for storage and comparison."""
    return username.strip().lower()


def validate_username(username: str) -> str | None:
    """Return an error message if username is invalid, else None."""
    normalized = normalize_username(username)
    if not normalized:
        return "Username is required."
    if len(normalized) < USERNAME_MIN_LENGTH or len(normalized) > USERNAME_MAX_LENGTH:
        return "Username must be 3-32 characters."
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        return "Username may only contain lowercase letters, numbers, dots, dashes, or underscores."
    return None


async def set_username(username: str) -> None:
    """Persist the normalized single-admin username."""
    await set_setting("username", normalize_username(username))


async def get_username() -> str | None:
    """Return the configured single-admin username."""
    return await get_setting("username")


async def check_password(password: str) -> bool:
    """Return True if password matches the stored hash."""
    stored = await get_setting("password_hash")
    if not stored:
        return False
    return verify_password(password, stored)


async def check_credentials(username: str, password: str) -> bool:
    """Return True if the provided username and password are valid.

    If a legacy install has a password hash but no username yet, the first
    successful password login claims the submitted username.
    """
    normalized_username = normalize_username(username)
    username_error = validate_username(normalized_username)
    if username_error is not None:
        return False

    if not await check_password(password):
        return False

    stored_username = await get_username()
    if stored_username is None:
        await set_username(normalized_username)
        return True

    return compare_digest(normalized_username, normalize_username(stored_username))


# ---------------------------------------------------------------------------
# Brute-force rate limiter (in-memory, resets on restart)
# ---------------------------------------------------------------------------

_login_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(request: Request) -> bool:
    """Return True if the client is allowed to attempt login."""
    ip = _client_ip(request)
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Remove attempts outside the window
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX_ATTEMPTS


def record_failed_login(request: Request) -> None:
    """Record a failed login attempt for rate limiting."""
    ip = _client_ip(request)
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts.append(now)
    _login_attempts[ip] = attempts


def clear_login_attempts(request: Request) -> None:
    """Clear login attempts on successful login."""
    ip = _client_ip(request)
    _login_attempts.pop(ip, None)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests to /login (or /setup if not set up)."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Any:
        path = request.url.path

        # Always allow public paths and static files
        if any(path.startswith(p) for p in _PUBLIC_PATHS):
            return await call_next(request)

        setup_done = await is_setup_complete()

        # Redirect to setup if not configured
        if not setup_done:
            return RedirectResponse(url="/setup", status_code=302)

        # Require valid session for all other routes
        if not await validate_session(request):
            return RedirectResponse(url="/login", status_code=302)

        return await call_next(request)

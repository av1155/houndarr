"""Authentication: password hashing, session management, login middleware."""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from hmac import compare_digest
from typing import Any

import bcrypt
from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from houndarr.config import get_settings
from houndarr.repositories.settings import get_setting, set_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SESSION_COOKIE_NAME = "houndarr_session"
CSRF_COOKIE_NAME = "houndarr_csrf"
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours
BCRYPT_COST = 12
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]+$")

# HTTP methods that mutate state and require CSRF protection.
_CSRF_PROTECTED_METHODS = frozenset(["POST", "PUT", "PATCH", "DELETE"])

# Routes that don't require authentication
_PUBLIC_PATHS = frozenset(
    [
        "/setup",
        "/login",
        "/api/health",
        "/static",
    ]
)

# Logout is a safe, destructive-free action (session invalidation only). We
# allow it without CSRF/session validation so stale legacy sessions can always
# be cleared after upgrades.
_LOGOUT_PATH = "/logout"


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
_setup_complete: bool | None = None


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


async def create_session(response: Response) -> str:
    """Create a new session, set the session cookie, and return a CSRF token.

    The session cookie is ``HttpOnly`` (JS cannot read it).  A separate
    CSRF cookie (``houndarr_csrf``) is set without ``HttpOnly`` so that
    JavaScript / HTMX can read it and include it in request headers.

    Args:
        response: The outgoing HTTP response to attach cookies to.

    Returns:
        The CSRF token string (also stored in the CSRF cookie).
    """
    settings = get_settings()
    serializer = await _get_serializer()
    csrf_token = secrets.token_hex(32)
    payload = {"ts": int(time.time()), "csrf": csrf_token}
    token = serializer.dumps(payload)

    cookie_kwargs: dict[str, Any] = {
        "max_age": SESSION_MAX_AGE_SECONDS,
        "httponly": True,
        "samesite": settings.cookie_samesite,
        "secure": settings.secure_cookies,
    }

    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, **cookie_kwargs)

    # CSRF cookie: readable by JS/HTMX, NOT httponly
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=False,
        samesite=settings.cookie_samesite,
        secure=settings.secure_cookies,
    )

    return csrf_token


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


async def get_session_csrf_token(request: Request) -> str | None:
    """Extract the CSRF token embedded in the signed session cookie.

    Returns:
        The CSRF token string, or ``None`` if the session is invalid/missing.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        serializer = await _get_serializer()
        payload: dict[str, Any] = serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        csrf: str | None = payload.get("csrf")
        return csrf
    except (SignatureExpired, BadSignature):
        return None


def clear_session(response: Response) -> None:
    """Delete the session and CSRF cookies."""
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)


# ---------------------------------------------------------------------------
# CSRF validation
# ---------------------------------------------------------------------------


async def validate_csrf(request: Request) -> bool:
    """Return True if the request carries a valid CSRF token.

    Accepts the token from either:
    - ``X-CSRF-Token`` request header (HTMX sends this when configured via
      ``hx-headers``), or
    - ``csrf_token`` form field (plain HTML form submissions).

    The token is compared against the one embedded in the signed session
    cookie using a constant-time comparison to prevent timing attacks.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``True`` if the CSRF token is present and valid; ``False`` otherwise.
    """
    expected = await get_session_csrf_token(request)
    if not expected:
        return False

    # Try header first (HTMX), then form body
    submitted = request.headers.get("X-CSRF-Token")
    if not submitted:
        # Form data requires us to peek at the body; HTMX always uses the header
        try:
            form = await request.form()
            submitted = form.get("csrf_token")  # type: ignore[assignment]
        except Exception:
            return False

    if not submitted:
        return False

    return compare_digest(str(submitted), expected)


# ---------------------------------------------------------------------------
# Setup state helpers
# ---------------------------------------------------------------------------


async def is_setup_complete() -> bool:
    """Return True if the initial password has been set.

    The result is cached once True because the password_hash setting is
    monotonic: it transitions from absent to present and is never deleted.
    """
    global _setup_complete  # noqa: PLW0603
    if _setup_complete is True:
        return True
    result = (await get_setting("password_hash")) is not None
    if result:
        _setup_complete = True
    return result


async def set_password(password: str) -> None:
    """Hash and persist the application password."""
    global _setup_complete  # noqa: PLW0603
    await set_setting("password_hash", hash_password(password))
    _setup_complete = True


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
    """Return the real client IP, honouring ``X-Forwarded-For`` only from
    configured trusted proxies.

    When ``HOUNDARR_TRUSTED_PROXIES`` is set (a comma-separated list of
    proxy IPs or CIDR subnets), and the direct connection IP matches one
    of those proxies or falls within a trusted subnet, the left-most IP
    in ``X-Forwarded-For`` is used as the client IP.

    When no trusted proxies are configured (the default), only
    ``request.client.host`` is used, preventing header spoofing.

    Args:
        request: The incoming HTTP request.

    Returns:
        The best-effort client IP string, or ``"unknown"`` as a fallback.
    """
    direct_ip = request.client.host if request.client else "unknown"
    settings = get_settings()
    trusted = settings.trusted_proxy_set()
    if trusted and direct_ip in trusted:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct_ip


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
# Proxy authentication helpers
# ---------------------------------------------------------------------------

# Paths that serve no purpose in proxy mode and redirect to the dashboard.
_PROXY_DEAD_PATHS = frozenset(["/setup", "/login"])


def _is_proxy_auth_mode() -> bool:
    """Return True if proxy-header authentication is active."""
    return get_settings().auth_mode == "proxy"


def _validate_proxy_auth(request: Request) -> str | None:
    """Extract the authenticated username from a proxy header.

    Security: the header is ONLY read after verifying the direct connection
    IP is in the configured trusted proxy set.  Untrusted IPs cannot spoof
    the header because it is never read for untrusted connections.

    Returns:
        The username string if the request is authenticated, or ``None``
        if authentication fails (untrusted IP, missing header, etc.).
    """
    settings = get_settings()
    direct_ip = request.client.host if request.client else "unknown"
    trusted = settings.trusted_proxy_set()

    # Gate 1: direct connection MUST originate from a trusted proxy
    if not trusted or direct_ip not in trusted:
        return None

    # Gate 2: the auth header must be present and non-empty
    username = request.headers.get(settings.auth_proxy_header, "").strip()
    if not username:
        return None

    return username


async def _validate_proxy_csrf(request: Request) -> bool:
    """Validate CSRF for proxy auth mode using double-submit cookie pattern.

    The CSRF cookie value must match the value submitted in the
    ``X-CSRF-Token`` header or ``csrf_token`` form field.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token:
        return False

    # Try header first (HTMX), then form body
    submitted = request.headers.get("X-CSRF-Token")
    if not submitted:
        try:
            form = await request.form()
            submitted = form.get("csrf_token")  # type: ignore[assignment]
        except Exception:
            return False

    if not submitted:
        return False

    return compare_digest(str(submitted), cookie_token)


def _ensure_proxy_csrf_cookie(request: Request, response: Response) -> None:
    """Set the CSRF cookie on an authenticated proxy-mode response if absent."""
    if request.cookies.get(CSRF_COOKIE_NAME):
        return
    settings = get_settings()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=secrets.token_hex(32),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=False,
        samesite=settings.cookie_samesite,
        secure=settings.secure_cookies,
    )


# ---------------------------------------------------------------------------
# Auth + CSRF middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce authentication and CSRF protection on all non-public routes.

    Supports two mutually exclusive authentication modes:

    **Builtin mode** (default):
        Session-based authentication.  Redirects unauthenticated requests to
        ``/login`` (or ``/setup`` if first-run setup has not been completed).

    **Proxy mode** (``HOUNDARR_AUTH_MODE=proxy``):
        Delegates authentication to a reverse proxy.  Requests are
        authenticated by a trusted header from a trusted proxy IP.  Requests
        from untrusted IPs receive ``403``; requests from trusted proxies
        without the auth header receive ``401``.

    CSRF protection is enforced in both modes.  State-changing requests
    (POST, PUT, PATCH, DELETE) must carry a valid CSRF token in either the
    ``X-CSRF-Token`` header or the ``csrf_token`` form field.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Any:
        path = request.url.path

        if _is_proxy_auth_mode():
            return await self._dispatch_proxy(request, call_next, path)
        return await self._dispatch_builtin(request, call_next, path)

    # ------------------------------------------------------------------
    # Builtin auth path (existing behaviour, unchanged)
    # ------------------------------------------------------------------

    async def _dispatch_builtin(
        self,
        request: Request,
        call_next: Callable[..., Any],
        path: str,
    ) -> Any:
        # Always allow logout so stale/broken sessions can be cleared
        if path == _LOGOUT_PATH and request.method == "POST":
            return await call_next(request)

        # Always allow public paths and static files
        if any(path.startswith(p) for p in _PUBLIC_PATHS):
            return await call_next(request)

        setup_done = await is_setup_complete()
        if not setup_done:
            return RedirectResponse(url="/setup", status_code=302)

        if not await validate_session(request):
            return RedirectResponse(url="/login", status_code=302)

        # CSRF check on state-changing methods
        if request.method in _CSRF_PROTECTED_METHODS and not await validate_csrf(request):
            logger.warning(
                "CSRF validation failed for %s %s from %s",
                request.method,
                path,
                _client_ip(request),
            )
            return HTMLResponse(
                content="<h1>403 Forbidden</h1><p>CSRF token invalid or missing.</p>",
                status_code=403,
            )

        return await call_next(request)

    # ------------------------------------------------------------------
    # Proxy auth path
    # ------------------------------------------------------------------

    async def _dispatch_proxy(
        self,
        request: Request,
        call_next: Callable[..., Any],
        path: str,
    ) -> Any:
        # Health check and static assets remain public
        if path.startswith("/api/health") or path.startswith("/static"):
            return await call_next(request)

        # Setup, login, and logout serve no purpose in proxy mode
        if path in _PROXY_DEAD_PATHS:
            return RedirectResponse(url="/", status_code=302)
        if path == _LOGOUT_PATH and request.method == "POST":
            response = RedirectResponse(url="/", status_code=302)
            response.delete_cookie(CSRF_COOKIE_NAME)
            return response

        # --- IP trust gate ---
        direct_ip = request.client.host if request.client else "unknown"
        settings = get_settings()
        trusted = settings.trusted_proxy_set()

        if not trusted or direct_ip not in trusted:
            logger.warning(
                "Proxy auth: blocked request from untrusted IP %s to %s",
                direct_ip,
                path,
            )
            return HTMLResponse(
                content=(
                    "<h1>403 Forbidden</h1>"
                    "<p>This Houndarr instance requires access through "
                    "an authenticating reverse proxy.</p>"
                ),
                status_code=403,
            )

        # --- Auth header gate ---
        username = request.headers.get(settings.auth_proxy_header, "").strip()
        if not username:
            logger.warning(
                "Proxy auth: missing header '%s' from trusted proxy %s for %s",
                settings.auth_proxy_header,
                direct_ip,
                path,
            )
            return HTMLResponse(
                content=(
                    "<h1>401 Unauthorized</h1>"
                    "<p>Authentication header missing. "
                    "Check your reverse proxy configuration.</p>"
                ),
                status_code=401,
            )

        # Authenticated: store username on request state for downstream use
        request.state.proxy_auth_user = username

        # CSRF check on state-changing methods
        if request.method in _CSRF_PROTECTED_METHODS and not await _validate_proxy_csrf(request):
            logger.warning(
                "CSRF validation failed (proxy mode) for %s %s from user %s",
                request.method,
                path,
                username,
            )
            return HTMLResponse(
                content="<h1>403 Forbidden</h1><p>CSRF token invalid or missing.</p>",
                status_code=403,
            )

        response = await call_next(request)

        # Ensure the CSRF cookie exists on every authenticated response
        _ensure_proxy_csrf_cookie(request, response)

        return response

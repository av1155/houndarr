"""Authentication package: password hashing, session, CSRF, proxy auth, middleware.

The auth surface is intentionally wide: password hashing, session
serialization, CSRF enforcement, first-run setup, proxy-auth trust
gate, rate limiting, and the ASGI middleware that composes them.
Each concern is being lifted out of this ``__init__.py`` into its
own submodule over the seven Phase 2 commits; every public name
remains importable from ``houndarr.auth`` for consumer stability.
"""

from __future__ import annotations

import logging

# time is re-imported here so tests can monkeypatch ``houndarr.auth.time.time``
# to freeze the clock across every call site (the ``time`` module is a
# singleton, so patching via the auth namespace still affects
# ``houndarr.auth.rate_limit``'s usage).
import time  # noqa: F401
from typing import Any

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from houndarr.auth import session as _session
from houndarr.auth import setup as _setup
from houndarr.auth.csrf import _CSRF_PROTECTED_METHODS, validate_csrf
from houndarr.auth.identity import resolve_signed_in_as
from houndarr.auth.password import BCRYPT_COST, hash_password, verify_password

# Proxy-auth seam: re-exported for the middleware's _dispatch_proxy
# path and for tests that monkeypatch the trust-gate composition.
from houndarr.auth.proxy_auth import (
    _PROXY_DEAD_PATHS,
    _ensure_proxy_csrf_cookie,
    _extract_proxy_username,
    _is_proxy_auth_mode,
    _is_trusted_proxy,
    _validate_proxy_auth,
    _validate_proxy_csrf,
)

# Rate-limit seam: re-exported for consumer stability.  Underscore-prefixed
# names are module-private by convention but are consumed by tests
# (``houndarr.auth._login_attempts``) and by routes that display client
# IPs (``_client_ip``).  Keeping them in ``__all__`` keeps mypy's
# explicit-export check green without updating every caller.
from houndarr.auth.rate_limit import (
    _LOGIN_MAX_ATTEMPTS,
    _LOGIN_WINDOW_SECONDS,
    _client_ip,
    _login_attempts,
    check_login_rate_limit,
    clear_login_attempts,
    record_failed_login,
    reset_login_attempts,
)

# Session seam: re-exported from the session submodule.  ``_serializer``
# deliberately stays OUT of this import list and is resolved through the
# module-level ``__getattr__`` below so ``houndarr.auth._serializer``
# always reads the current value in ``houndarr.auth.session`` instead of
# a stale None bound at package-import time.
from houndarr.auth.session import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    _get_serializer,
    clear_session,
    create_session,
    get_session_csrf_token,
    validate_session,
)

# Setup seam: re-exported from the setup submodule.  ``_setup_complete``
# and ``_USERNAME_PATTERN`` pass through ``__getattr__`` alongside
# ``_serializer`` so tests that inspect the module's state always read
# the authoritative value.
from houndarr.auth.setup import (
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    check_credentials,
    check_password,
    get_username,
    is_setup_complete,
    normalize_username,
    reset_auth_caches,
    rotate_session_secret,
    set_password,
    set_username,
    validate_username,
)
from houndarr.config import get_settings

__all__ = [
    "BCRYPT_COST",
    "CSRF_COOKIE_NAME",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE_SECONDS",
    "USERNAME_MAX_LENGTH",
    "USERNAME_MIN_LENGTH",
    "_CSRF_PROTECTED_METHODS",
    "_LOGIN_MAX_ATTEMPTS",
    "_LOGIN_WINDOW_SECONDS",
    "_PROXY_DEAD_PATHS",
    "_client_ip",
    "_ensure_proxy_csrf_cookie",
    "_extract_proxy_username",
    "_get_serializer",
    "_is_proxy_auth_mode",
    "_is_trusted_proxy",
    "_login_attempts",
    "_validate_proxy_auth",
    "_validate_proxy_csrf",
    "check_credentials",
    "check_login_rate_limit",
    "check_password",
    "clear_login_attempts",
    "clear_session",
    "create_session",
    "get_session_csrf_token",
    "get_username",
    "hash_password",
    "is_setup_complete",
    "normalize_username",
    "record_failed_login",
    "reset_auth_caches",
    "reset_login_attempts",
    "resolve_signed_in_as",
    "rotate_session_secret",
    "set_password",
    "set_username",
    "validate_csrf",
    "validate_session",
    "validate_username",
    "verify_password",
]


def __getattr__(name: str) -> Any:
    """Resolve state-bearing globals live from their owning submodule.

    Package-level ``from X import Y`` binds Y at import time and misses
    later re-assignments inside the owning submodule.  For the globals
    tests and external callers inspect (``_serializer``,
    ``_setup_complete``, ``_USERNAME_PATTERN``), routing attribute
    access through ``__getattr__`` keeps ``houndarr.auth.<name>``
    showing the authoritative value every time.
    """
    if name == "_serializer":
        return _session._serializer
    if name == "_setup_complete":
        return _setup._setup_complete
    if name == "_USERNAME_PATTERN":
        return _setup._USERNAME_PATTERN
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (middleware-facing; USERNAME_*, SESSION_* and
# _CSRF_PROTECTED_METHODS live in their owning submodules)
# ---------------------------------------------------------------------------

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

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Route each request to the proxy-auth or built-in auth path.

        Per-request dispatch keeps the middleware thin and lets each
        branch handle its own public-path and CSRF rules.
        """
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
        call_next: RequestResponseEndpoint,
        path: str,
    ) -> Response:
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
        call_next: RequestResponseEndpoint,
        path: str,
    ) -> Response:
        # Health check and static assets remain public
        if path.startswith("/api/health") or path.startswith("/static"):
            return await call_next(request)

        # Setup, login, and logout serve no purpose in proxy mode
        if path in _PROXY_DEAD_PATHS:
            return RedirectResponse(url="/", status_code=302)
        if path == _LOGOUT_PATH and request.method == "POST":
            logout_response = RedirectResponse(url="/", status_code=302)
            logout_response.delete_cookie(CSRF_COOKIE_NAME)
            return logout_response

        # --- IP trust gate ---
        if not _is_trusted_proxy(request):
            direct_ip = request.client.host if request.client else "unknown"
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
        username = _extract_proxy_username(request)
        if username is None:
            direct_ip = request.client.host if request.client else "unknown"
            logger.warning(
                "Proxy auth: missing header '%s' from trusted proxy %s for %s",
                get_settings().auth_proxy_header,
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

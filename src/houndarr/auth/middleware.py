"""ASGI middleware that composes every auth seam.

Two dispatch branches share the same middleware: ``_dispatch_builtin``
for session-cookie mode (the default) and ``_dispatch_proxy`` for
reverse-proxy / SSO mode.  Each branch reads its public-path list and
then delegates trust, identity, and CSRF decisions to the appropriate
seam submodule.

Helpers are called through their owning modules (for example
``proxy_auth._is_trusted_proxy(request)`` instead of a direct
``from .proxy_auth import _is_trusted_proxy`` binding) so tests that
monkeypatch the module attribute propagate the patch into the
middleware without also having to update the import site.
"""

from __future__ import annotations

import logging

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from houndarr.auth import proxy_auth as _proxy_auth
from houndarr.auth import rate_limit as _rate_limit
from houndarr.auth.csrf import _CSRF_PROTECTED_METHODS, validate_csrf
from houndarr.auth.session import CSRF_COOKIE_NAME, validate_session
from houndarr.auth.setup import is_setup_complete
from houndarr.config import get_settings

logger = logging.getLogger(__name__)

# Routes that don't require authentication
_PUBLIC_PATHS = frozenset(
    [
        "/setup",
        "/login",
        "/api/health",
        "/static",
    ]
)

# Logout is a safe, destructive-free action (session invalidation only).
# We allow it without CSRF/session validation so stale legacy sessions
# can always be cleared after upgrades.
_LOGOUT_PATH = "/logout"


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

        if _proxy_auth._is_proxy_auth_mode():
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
                _rate_limit._client_ip(request),
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
        if path in _proxy_auth._PROXY_DEAD_PATHS:
            return RedirectResponse(url="/", status_code=302)
        if path == _LOGOUT_PATH and request.method == "POST":
            logout_response = RedirectResponse(url="/", status_code=302)
            logout_response.delete_cookie(CSRF_COOKIE_NAME)
            return logout_response

        # --- IP trust gate ---
        if not _proxy_auth._is_trusted_proxy(request):
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
        username = _proxy_auth._extract_proxy_username(request)
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
        if request.method in _CSRF_PROTECTED_METHODS and not await _proxy_auth._validate_proxy_csrf(
            request
        ):
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
        _proxy_auth._ensure_proxy_csrf_cookie(request, response)

        return response

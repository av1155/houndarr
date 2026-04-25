"""Proxy-authentication helpers for reverse-proxy / SSO deployments.

Proxy mode (``HOUNDARR_AUTH_MODE=proxy``) delegates identity to a
reverse proxy that injects the authenticated username into a
configured header (``HOUNDARR_AUTH_PROXY_HEADER``, default
``Remote-User``).  This module owns every primitive the middleware
composes to decide whether a request should be admitted:

- :func:`_is_trusted_proxy` gates the header read on the direct
  connection IP being inside the configured trusted-proxy set.
- :func:`_extract_proxy_username` reads the header AFTER the trust
  check; it MUST NOT be called independently on untrusted input.
- :func:`_validate_proxy_auth` composes the two for callers that only
  need the binary authenticated-or-not answer.

CSRF in proxy mode uses a double-submit pattern (cookie value must
match submitted token) because there is no server-signed session
cookie to embed the token in; :func:`_validate_proxy_csrf` and
:func:`_ensure_proxy_csrf_cookie` own that state.
"""

from __future__ import annotations

import secrets
from hmac import compare_digest

from fastapi import Request, Response

from houndarr.auth.session import CSRF_COOKIE_NAME, SESSION_MAX_AGE_SECONDS
from houndarr.config import get_settings

# Paths that serve no purpose in proxy mode and redirect to the dashboard.
_PROXY_DEAD_PATHS = frozenset(["/setup", "/login"])


def _is_proxy_auth_mode() -> bool:
    """Return True if proxy-header authentication is active."""
    return get_settings().auth_mode == "proxy"


def _is_trusted_proxy(request: Request) -> bool:
    """Return True when the direct connection IP is in the trusted proxy set.

    Security: ``trusted_proxy_set()`` returning an empty set means the
    operator has not configured any trusted proxy, in which case every
    request is untrusted regardless of the direct IP.  This guards
    against header spoofing by untrusted clients: callers that read the
    auth header must gate the read behind this check.
    """
    settings = get_settings()
    trusted = settings.trusted_proxy_set()
    if not trusted:
        return False
    direct_ip = request.client.host if request.client else "unknown"
    return direct_ip in trusted


def _extract_proxy_username(request: Request) -> str | None:
    """Return the proxy-supplied username, or ``None`` if the header is absent.

    Assumes the caller has already verified the direct IP is trusted via
    :func:`_is_trusted_proxy`; this function does not re-check.
    """
    settings = get_settings()
    username = request.headers.get(settings.auth_proxy_header, "").strip()
    return username or None


def _validate_proxy_auth(request: Request) -> str | None:
    """Return the authenticated proxy username, or ``None`` on any failure.

    Composes :func:`_is_trusted_proxy` (direct IP must be in the trusted
    set) and :func:`_extract_proxy_username` (header must be present and
    non-empty).  Both primitives are the single source of truth for the
    middleware's ``_dispatch_proxy``; this helper is the convenience form
    for callers that only need the binary "authenticated?" answer.
    """
    if not _is_trusted_proxy(request):
        return None
    return _extract_proxy_username(request)


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

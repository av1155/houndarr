"""Brute-force rate limiter for the login route.

In-memory sliding-window counter keyed on the direct-connection IP
(or the left-most ``X-Forwarded-For`` entry when the connection
comes from a configured trusted proxy).  The bucket resets on
process restart; long-term lockout is not a goal, short-term
friction against credential stuffing is.

The module also owns ``_client_ip`` because the rate-limit and
proxy-auth dispatch both read the same real-client IP; ``auth.py``
previously housed both next to each other and the test surface
keeps pinning the helper here.
"""

from __future__ import annotations

import time

from fastapi import Request

from houndarr.config import get_settings

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


def reset_login_attempts() -> None:
    """Drop every tracked bucket.

    Called from the factory-reset path in the setup seam.  Kept as a
    dedicated helper (rather than ``_login_attempts.clear()`` inline
    at the call site) so cross-seam callers never reach into another
    seam's module-private dict.
    """
    _login_attempts.clear()

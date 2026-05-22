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

import asyncio
import logging
import time

from fastapi import Request

from houndarr.config import get_settings

logger = logging.getLogger(__name__)

_login_attempts: dict[str, list[float]] = {}
_widget_key_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 60
_RATE_LIMIT_SWEEP_INTERVAL_SECONDS = 5 * 60


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


def _check_rate_limit(bucket: dict[str, list[float]], request: Request) -> bool:
    """Return whether *request* may try another authentication attempt.

    Filters timestamps older than ``_LOGIN_WINDOW_SECONDS`` out of the
    per-IP bucket.  When pruning leaves the bucket empty the IP key is
    popped instead of being written back as ``[]``; without this, every
    probe from a unique source IP would leave a permanent empty entry
    and the dict would grow with the count of distinct IPs that have
    ever hit ``/login`` (issue #632).
    """
    ip = _client_ip(request)
    now = time.time()
    attempts = bucket.get(ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
    if attempts:
        bucket[ip] = attempts
    else:
        bucket.pop(ip, None)
    return len(attempts) < _LOGIN_MAX_ATTEMPTS


def _record_failed_attempt(bucket: dict[str, list[float]], request: Request) -> None:
    """Record one failed authentication attempt in *bucket*."""
    ip = _client_ip(request)
    now = time.time()
    attempts = bucket.get(ip, [])
    attempts.append(now)
    bucket[ip] = attempts


def _sweep_bucket(bucket: dict[str, list[float]], now: float) -> int:
    """Evict IP buckets whose timestamps have all expired.

    ``_check_rate_limit`` already evicts the requesting IP on read, but
    a scanner that hits ``/login`` once per source IP and never returns
    leaves ``[t1]`` behind forever (issue #632).  The periodic sweep
    walks both buckets and pops every entry whose most-recent timestamp
    is older than the rate-limit window.

    ``_record_failed_attempt`` appends monotonically, so the last
    element is the youngest; checking ``attempts[-1]`` is sufficient.
    The stale-key list is materialised before mutating ``bucket`` to
    avoid ``RuntimeError: dictionary changed size during iteration``.

    Returns the count of evicted IP entries (for log gating).
    """
    stale_ips = [
        ip
        for ip, attempts in bucket.items()
        if not attempts or now - attempts[-1] >= _LOGIN_WINDOW_SECONDS
    ]
    for ip in stale_ips:
        bucket.pop(ip, None)
    return len(stale_ips)


async def periodic_rate_limit_sweep() -> None:
    """Evict stale rate-limit buckets on a fixed cadence.

    Cancellation is propagated so the lifespan shutdown path can stop
    the task cleanly; any other exception is logged and the loop keeps
    running so a transient error never silently disables rate-limit GC.
    """
    while True:
        await asyncio.sleep(_RATE_LIMIT_SWEEP_INTERVAL_SECONDS)
        try:
            now = time.time()
            login_evicted = _sweep_bucket(_login_attempts, now)
            widget_evicted = _sweep_bucket(_widget_key_attempts, now)
            if login_evicted or widget_evicted:
                logger.debug(
                    "Rate-limit sweep evicted %d login and %d widget-key buckets",
                    login_evicted,
                    widget_evicted,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Rate-limit sweep task failed")


def check_login_rate_limit(request: Request) -> bool:
    """Return True if the client is allowed to attempt login."""
    return _check_rate_limit(_login_attempts, request)


def record_failed_login(request: Request) -> None:
    """Record a failed login attempt for rate limiting."""
    _record_failed_attempt(_login_attempts, request)


def check_widget_key_rate_limit(request: Request) -> bool:
    """Return True if the client is allowed to try a widget API key."""
    return _check_rate_limit(_widget_key_attempts, request)


def record_failed_widget_key_attempt(request: Request) -> None:
    """Record a failed widget API key attempt for rate limiting."""
    _record_failed_attempt(_widget_key_attempts, request)


def clear_login_attempts(request: Request) -> None:
    """Clear login attempts on successful login."""
    ip = _client_ip(request)
    _login_attempts.pop(ip, None)


def clear_widget_key_attempts(request: Request) -> None:
    """Clear widget key attempts on successful API key verification."""
    ip = _client_ip(request)
    _widget_key_attempts.pop(ip, None)


def reset_login_attempts() -> None:
    """Drop every tracked bucket.

    Called from the factory-reset path in the setup seam.  Kept as a
    dedicated helper (rather than ``_login_attempts.clear()`` inline
    at the call site) so cross-seam callers never reach into another
    seam's module-private dict.
    """
    _login_attempts.clear()


def reset_widget_key_attempts() -> None:
    """Drop every tracked widget API key failure bucket."""
    _widget_key_attempts.clear()

"""Session cookies and the signed CSRF payload they carry.

The session cookie is an ``itsdangerous``-signed JSON blob with a
timestamp and a per-session CSRF token.  A sibling ``houndarr_csrf``
cookie carries the plaintext CSRF token so HTMX can read it from
JavaScript and include it in the ``X-CSRF-Token`` request header; the
middleware cross-checks the two.

``_get_serializer`` lazy-loads a ``URLSafeTimedSerializer`` keyed on
the DB-stored ``session_secret`` the first time a cookie needs to be
signed or validated.  The setup seam's ``rotate_session_secret`` calls
:func:`reset_serializer` after persisting a new secret so every existing
cookie becomes unverifiable; that is how Houndarr invalidates every
other signed-in tab after a password change.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from houndarr.config import get_settings
from houndarr.repositories.settings import get_setting, set_setting

SESSION_COOKIE_NAME = "houndarr_session"
CSRF_COOKIE_NAME = "houndarr_csrf"
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours

_serializer: URLSafeTimedSerializer | None = None


async def _get_serializer() -> URLSafeTimedSerializer:
    """Return the lazily-initialized session serializer.

    First call generates (or reads) the DB-persisted ``session_secret``;
    every subsequent call reuses the cached serializer until
    :func:`reset_serializer` clears it.
    """
    global _serializer  # noqa: PLW0603
    if _serializer is None:
        secret = await get_setting("session_secret")
        if not secret:
            secret = os.urandom(32).hex()
            await set_setting("session_secret", secret)
        _serializer = URLSafeTimedSerializer(secret, salt="session")
    return _serializer


def reset_serializer() -> None:
    """Drop the cached serializer so the next call re-reads the secret.

    Composed by ``setup.rotate_session_secret`` (post-password-change)
    and ``reset_auth_caches`` (factory reset) so cross-seam callers
    never touch this module's global directly.
    """
    global _serializer  # noqa: PLW0603
    _serializer = None


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

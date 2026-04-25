"""CSRF validation for the built-in-auth middleware path.

Double-submit style: the session cookie carries a signed CSRF token
(see :mod:`houndarr.auth.session`); HTMX (or a plain form submission)
must echo the same token back via ``X-CSRF-Token`` or the
``csrf_token`` form field.  This module owns the comparison.

Proxy-auth mode uses a different shape (cookie + header double-submit,
no session cookie) and lives in :mod:`houndarr.auth.proxy_auth`.
"""

from __future__ import annotations

from hmac import compare_digest

from fastapi import Request

from houndarr.auth.session import get_session_csrf_token

# HTTP methods that mutate state and require CSRF protection.
_CSRF_PROTECTED_METHODS = frozenset(["POST", "PUT", "PATCH", "DELETE"])


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

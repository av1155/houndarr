"""Identity resolution for the signed-in admin.

Composes the proxy-auth and setup seams: when proxy mode is active
the forwarded header wins, otherwise the builtin single-admin
username (stored in ``settings.username``) is used.  The
"Signed in as" chip on the Settings page is the only consumer.
"""

from __future__ import annotations

from fastapi import Request

from houndarr.auth.proxy_auth import _is_proxy_auth_mode
from houndarr.auth.setup import get_username


async def resolve_signed_in_as(request: Request) -> str:
    """Return the identity label for the signed-in admin.

    In proxy auth mode this is the username the upstream proxy forwarded
    (stashed on ``request.state.proxy_auth_user`` by the middleware); in
    builtin mode it is the configured single-admin username. Falls back to
    ``"admin"`` when no other value is available, so templates always have
    something meaningful to render.
    """
    if _is_proxy_auth_mode():
        proxy_user = getattr(request.state, "proxy_auth_user", None)
        if proxy_user:
            return str(proxy_user)
    stored = await get_username()
    return stored or "admin"

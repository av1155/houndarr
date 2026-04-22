"""Account routes under /settings/account/*.

Currently hosts only the admin password change endpoint; lives in its
own module so future account-scoped routes (signed-in-as preview,
proxy identity display) have a natural home without bloating instances.py.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from houndarr.auth import (
    _client_ip,
    check_login_rate_limit,
    check_password,
    clear_login_attempts,
    create_session,
    record_failed_login,
    rotate_session_secret,
    set_password,
)
from houndarr.routes.settings._helpers import render_settings_page

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/settings/account/password", response_class=HTMLResponse)
async def account_password_update(
    request: Request,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    new_password_confirm: Annotated[str, Form()],
) -> HTMLResponse:
    """Update admin password from the settings page."""
    # Rate limit: the same IP-scoped bucket that guards /login, so a stolen
    # session cannot brute-force the current password through this endpoint.
    if not check_login_rate_limit(request):
        return await render_settings_page(
            request,
            status_code=429,
            account_error="Too many password attempts. Try again in a minute.",
        )

    if not await check_password(current_password):
        record_failed_login(request)
        logger.warning(
            "Password change rejected: incorrect current_password from %s",
            _client_ip(request),
        )
        return await render_settings_page(
            request,
            status_code=422,
            account_error="Current password is incorrect.",
        )

    if len(new_password) < 8:
        return await render_settings_page(
            request,
            status_code=422,
            account_error="New password must be at least 8 characters.",
        )

    if new_password != new_password_confirm:
        return await render_settings_page(
            request,
            status_code=422,
            account_error="New passwords do not match.",
        )

    if new_password == current_password:
        return await render_settings_page(
            request,
            status_code=422,
            account_error="New password must be different from current password.",
        )

    # Clear the failed-attempt bucket on a successful credential check
    # before the persistence step so a transient DB error in set_password
    # doesn't leave the counter inflated against the admin who just
    # entered the right password.
    clear_login_attempts(request)
    await set_password(new_password)
    # Rotate the session-signing secret so any previously issued cookie
    # (stolen or otherwise) stops validating. The current admin still
    # expects to stay signed in on the tab they made the change from, so
    # re-issue a session cookie on the outgoing response.
    await rotate_session_secret()
    response = await render_settings_page(
        request,
        account_success="Password updated successfully.",
    )
    await create_session(response)
    # The response body was rendered from the incoming (pre-rotation)
    # cookies, so every hidden csrf_token input and the body-level
    # hx-headers attribute stamped by app.js at initial page load are
    # stale relative to the cookies we just issued. Force a full reload so
    # HTMX re-stamps hx-headers from the fresh cookie and every form
    # renders with the new csrf_token; without this, the next mutating
    # HTMX request from the tab would 403 until the admin manually
    # refreshed.
    response.headers["HX-Refresh"] = "true"
    return response

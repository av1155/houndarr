"""Admin routes: bulk destructive operations behind Settings > Admin.

Three POST endpoints back the UI actions that live inside the ``Admin``
collapsible on the Settings page:

* ``/settings/admin/reset-instances``: revert every instance's policy
  columns back to :mod:`houndarr.config` defaults (preserves identity
  + snapshot counts).
* ``/settings/admin/clear-logs``: truncate the ``search_log`` table and
  leave a single audit row.
* ``/settings/admin/factory-reset``: wipe the database and master key
  back to first-run state. Requires typed-phrase + password (builtin)
  or typed-phrase + echoed proxy username (proxy mode).

All three are CSRF-protected and auth-gated by :mod:`houndarr.auth`'s
middleware, so no per-route guard is needed here.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from houndarr import __version__
from houndarr.auth import (
    CSRF_COOKIE_NAME,
    _client_ip,
    check_login_rate_limit,
    check_password,
    clear_login_attempts,
    clear_session,
    record_failed_login,
)
from houndarr.config import get_settings
from houndarr.routes._templates import get_templates
from houndarr.services.admin import (
    clear_all_search_logs,
    factory_reset,
    request_process_exit,
    reset_all_instance_policy,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _flash_response(
    request: Request,
    *,
    tone: str,
    message: str,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the small toast partial swapped into ``#admin-flash``.

    ``tone`` is ``"success"``, ``"danger"``, or ``"info"`` and maps to the
    existing alert palette in ``base.html``. Status code ``422`` is used
    for validation failures so HTMX opts into the swap (see the htmx-config
    meta in ``base.html``).
    """
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    return get_templates().TemplateResponse(
        request=request,
        name="partials/admin/flash.html",
        context={
            "tone": tone,
            "message": message,
            "csrf_token": csrf_token,
            "version": __version__,
        },
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Reset all instance settings
# ---------------------------------------------------------------------------


@router.post("/settings/admin/reset-instances", response_class=HTMLResponse)
async def admin_reset_instances(request: Request) -> HTMLResponse:
    """Revert every instance's policy columns back to :mod:`config` defaults."""
    supervisor = getattr(request.app.state, "supervisor", None)
    master_key: bytes = request.app.state.master_key
    count = await reset_all_instance_policy(master_key=master_key, supervisor=supervisor)

    if count == 0:
        message = "No instances configured: nothing to reset."
    else:
        message = (
            f"Policy settings reset to defaults for {count} instance{'s' if count != 1 else ''}."
        )
    return _flash_response(request, tone="success", message=message)


# ---------------------------------------------------------------------------
# Clear all logs
# ---------------------------------------------------------------------------


@router.post("/settings/admin/clear-logs", response_class=HTMLResponse)
async def admin_clear_logs(request: Request) -> HTMLResponse:
    """Truncate ``search_log`` and leave a single audit breadcrumb."""
    removed = await clear_all_search_logs()
    if removed == 0:
        message = "Logs were already empty."
    else:
        message = f"Cleared {removed} log row{'s' if removed != 1 else ''}."
    return _flash_response(request, tone="success", message=message)


# ---------------------------------------------------------------------------
# Factory reset
# ---------------------------------------------------------------------------


_FACTORY_RESET_PHRASE = "RESET"


@router.post("/settings/admin/factory-reset")
async def admin_factory_reset(
    request: Request,
    confirm_phrase: Annotated[str, Form()] = "",
    current_password: Annotated[str, Form()] = "",
    confirm_username: Annotated[str, Form()] = "",
) -> Response:
    """Wipe the database and master key; return the client to setup."""
    settings = get_settings()
    is_proxy = settings.auth_mode == "proxy"

    if confirm_phrase.strip() != _FACTORY_RESET_PHRASE:
        return _flash_response(
            request,
            tone="danger",
            message=f"Type {_FACTORY_RESET_PHRASE} to confirm a factory reset.",
            status_code=422,
        )

    effective_user: str
    if is_proxy:
        proxy_user = getattr(request.state, "proxy_auth_user", None)
        if not proxy_user:
            # Defensive: middleware should have populated this for a request
            # that reached the endpoint. Treat as a CSRF/auth failure mode.
            return _flash_response(
                request,
                tone="danger",
                message="Could not verify proxy identity; refresh and try again.",
                status_code=422,
            )
        if not hmac.compare_digest(confirm_username.strip().lower(), proxy_user.lower()):
            return _flash_response(
                request,
                tone="danger",
                message=f"Typed username does not match '{proxy_user}'.",
                status_code=422,
            )
        effective_user = proxy_user
    else:
        # Rate limit: factory reset is a destructive action. Share the IP-
        # scoped bucket with /login so a stolen session cannot brute-force
        # the admin password through this endpoint either.
        if not check_login_rate_limit(request):
            return _flash_response(
                request,
                tone="danger",
                message="Too many attempts. Try again in a minute.",
                status_code=429,
            )
        if not current_password or not await check_password(current_password):
            record_failed_login(request)
            logger.warning(
                "Factory reset rejected: incorrect current_password from %s",
                _client_ip(request),
            )
            return _flash_response(
                request,
                tone="danger",
                message="Current password is incorrect.",
                status_code=422,
            )
        clear_login_attempts(request)
        effective_user = "admin"

    logger.warning(
        "Factory reset triggered by %s from %s (auth_mode=%s)",
        effective_user,
        _client_ip(request),
        settings.auth_mode,
    )

    redirect_target = "/" if is_proxy else "/setup"

    response = Response(status_code=200)
    response.headers["HX-Redirect"] = redirect_target
    clear_session(response)

    try:
        await factory_reset(app=request.app, data_dir=settings.data_dir)
    except Exception:  # noqa: BLE001
        # Hybrid fallback: schedule a delayed process exit so this response
        # reaches the client before the container restarts. The orchestrator
        # brings Houndarr back up with a clean data_dir and the redirect
        # target is reachable again.
        logger.exception("Factory reset: in-process path failed; scheduling process exit")

        async def _delayed_exit() -> None:
            # 1.5s gives the HX-Redirect response time to flush over slow
            # connections (mobile, tunnelled) before the container exits.
            await asyncio.sleep(1.5)
            request_process_exit()

        # Fire-and-forget: RUF006's "keep a reference" rule doesn't apply
        # to the hybrid fallback because the entire event loop dies inside
        # request_process_exit(). There is nothing to keep alive past the
        # os._exit() call.
        asyncio.create_task(_delayed_exit())  # noqa: RUF006

    return response

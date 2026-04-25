"""Shared helpers used across the settings sub-routers.

Centralises template rendering, instance form validation, client
construction, the connection test flow, and the ``_render_settings_page``
composition that GET /settings and the password change route both
delegate to.  Sub-modules (``page``, ``account``, ``instances``) import
only what they need from here; direct FastAPI app code still imports
the composed router from ``houndarr.routes.settings``.
"""

from __future__ import annotations

import html
import logging

from fastapi import Request
from fastapi.responses import HTMLResponse

from houndarr import __version__
from houndarr.auth import resolve_signed_in_as
from houndarr.config import (
    DEFAULT_ALLOWED_TIME_WINDOW,
    DEFAULT_BATCH_SIZE,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_BATCH_SIZE,
    DEFAULT_CUTOFF_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_HOURLY_CAP,
    DEFAULT_HOURLY_CAP,
    DEFAULT_LIDARR_SEARCH_MODE,
    DEFAULT_POST_RELEASE_GRACE_HOURS,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_READARR_SEARCH_MODE,
    DEFAULT_SEARCH_ORDER,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_SONARR_SEARCH_MODE,
    DEFAULT_WHISPARR_V2_SEARCH_MODE,
    get_settings,
)
from houndarr.routes._htmx import (
    hx_retarget_response,
    hx_trigger_response,
    is_hx_request,
)
from houndarr.routes._templates import get_templates
from houndarr.services.instance_validation import (
    API_KEY_UNCHANGED,
    ConnectionCheck,
    SearchModes,
    build_client,
    check_connection,
    resolve_search_modes,
    type_mismatch_message,
    validate_cutoff_controls,
    validate_upgrade_controls,
)
from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    LidarrSearchMode,
    MissingPolicy,
    ReadarrSearchMode,
    RuntimeSnapshot,
    SchedulePolicy,
    SearchOrder,
    SonarrSearchMode,
    UpgradePolicy,
    WhisparrV2SearchMode,
    active_error_instance_ids,
    list_instances,
)

# Re-exports from houndarr.services.instance_validation so existing
# route-layer imports through this module keep working.  The real
# definitions now live in the service so instance_submit can depend
# on the service layer instead of reaching back into routes.
__all__ = [
    "API_KEY_UNCHANGED",
    "ConnectionCheck",
    "SearchModes",
    "blank_instance",
    "build_client",
    "check_connection",
    "connection_guard_response",
    "connection_status_response",
    "master_key",
    "render",
    "render_settings_page",
    "resolve_search_modes",
    "type_mismatch_message",
    "validate_cutoff_controls",
    "validate_upgrade_controls",
]

logger = logging.getLogger(__name__)


def render(
    request: Request,
    template_name: str,
    status_code: int = 200,
    **ctx: object,
) -> HTMLResponse:
    """Render a Jinja2 template with the CSRF token and app version injected."""
    from houndarr.auth import CSRF_COOKIE_NAME

    csrf_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    context = {"version": __version__, "csrf_token": csrf_token, **ctx}
    return get_templates().TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
    )


def master_key(request: Request) -> bytes:
    """Return the Fernet master key stored on ``app.state``.

    Thin wrapper over :func:`houndarr.deps.get_master_key` so the
    legacy helper call site (``render_settings_page``) inherits the
    same 503 "Master key unavailable" failure class that the
    Depends-migrated routes already use.  The direct
    ``request.app.state.master_key`` read this replaced would have
    returned ``None`` or a wrongly-typed value during a lifespan
    race (factory-reset window, misconfigured test harness) and
    pushed the failure into the Fernet layer as a less actionable
    error; routing through the shim keeps the behaviour
    symmetrical across the routes tree.
    """
    from houndarr.deps import get_master_key

    return get_master_key(request)


def blank_instance() -> Instance:
    """Return an Instance pre-filled with defaults for the add-form partial."""
    return Instance(
        core=InstanceCore(
            id=0,
            name="",
            type=InstanceType.radarr,
            url="",
            api_key="",
            enabled=True,
        ),
        missing=MissingPolicy(
            batch_size=DEFAULT_BATCH_SIZE,
            sleep_interval_mins=DEFAULT_SLEEP_INTERVAL_MINUTES,
            hourly_cap=DEFAULT_HOURLY_CAP,
            cooldown_days=DEFAULT_COOLDOWN_DAYS,
            post_release_grace_hrs=DEFAULT_POST_RELEASE_GRACE_HOURS,
            queue_limit=DEFAULT_QUEUE_LIMIT,
            sonarr_search_mode=SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE),
            lidarr_search_mode=LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE),
            readarr_search_mode=ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE),
            whisparr_v2_search_mode=WhisparrV2SearchMode(DEFAULT_WHISPARR_V2_SEARCH_MODE),
        ),
        cutoff=CutoffPolicy(
            cutoff_enabled=False,
            cutoff_batch_size=DEFAULT_CUTOFF_BATCH_SIZE,
            cutoff_cooldown_days=DEFAULT_CUTOFF_COOLDOWN_DAYS,
            cutoff_hourly_cap=DEFAULT_CUTOFF_HOURLY_CAP,
        ),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(
            allowed_time_window=DEFAULT_ALLOWED_TIME_WINDOW,
            search_order=SearchOrder(DEFAULT_SEARCH_ORDER),
        ),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="",
            updated_at="",
        ),
    )


def connection_status_response(message: str, ok: bool, status_code: int) -> HTMLResponse:
    """Render the inline connection-test status snippet for HTMX swap."""
    trigger = "houndarr-connection-test-success" if ok else "houndarr-connection-test-failure"
    color = "text-green-400" if ok else "text-red-400"
    return hx_trigger_response(
        HTMLResponse(
            content=f'<span class="text-xs {color}">{html.escape(message)}</span>',
            status_code=status_code,
        ),
        trigger,
    )


def connection_guard_response(message: str) -> HTMLResponse:
    """Re-target an error to the connection status span when a save is blocked."""
    return hx_retarget_response(
        HTMLResponse(
            content=f'<span class="text-xs text-red-400">{html.escape(message)}</span>',
            status_code=422,
        ),
        target="#instance-connection-status",
        reswap="innerHTML",
        trigger="houndarr-connection-test-failure",
    )


async def render_settings_page(
    request: Request,
    *,
    status_code: int = 200,
    account_error: str | None = None,
    account_success: str | None = None,
) -> HTMLResponse:
    """Render the settings page with common account and instance context."""
    from houndarr.repositories.settings import get_setting

    instances = await list_instances(master_key=master_key(request))
    error_ids = await active_error_instance_ids()
    # signed_in_as covers both builtin (local admin username) and proxy
    # mode (forwarded auth header). The template renders it verbatim so
    # the Admin > Security card never shows a stale or generic label.
    signed_in_as = await resolve_signed_in_as(request)
    changelog_popups_enabled = (await get_setting("changelog_popups_disabled")) != "1"
    update_check_enabled = (await get_setting("update_check_enabled")) == "1"
    template_name = (
        "partials/pages/settings_content.html" if is_hx_request(request) else "settings.html"
    )
    return render(
        request,
        template_name,
        status_code=status_code,
        instances=instances,
        active_error_ids=error_ids,
        signed_in_as=signed_in_as,
        auth_mode=get_settings().auth_mode,
        account_error=account_error,
        account_success=account_success,
        changelog_popups_enabled=changelog_popups_enabled,
        update_check_enabled=update_check_enabled,
    )

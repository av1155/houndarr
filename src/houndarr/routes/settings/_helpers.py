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
from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from houndarr.clients.whisparr_v3 import WhisparrV3Client
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
from houndarr.routes._htmx import is_hx_request
from houndarr.routes._templates import get_templates
from houndarr.services.instance_validation import (
    ConnectionCheck,
    SearchModes,
    resolve_search_modes,
    type_mismatch_message,
    validate_cutoff_controls,
    validate_upgrade_controls,
)
from houndarr.services.instances import (
    Instance,
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SearchOrder,
    SonarrSearchMode,
    WhisparrV2SearchMode,
    active_error_instance_ids,
    list_instances,
)

# Re-exported from houndarr.services.instance_validation so existing
# imports from this module keep working; D.11 moved the real definitions
# into the service layer.
__all__ = [
    "API_KEY_UNCHANGED",
    "ArrClient",
    "ConnectionCheck",
    "SearchModes",
    "active_error_instance_ids",
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

API_KEY_UNCHANGED = "__UNCHANGED__"
"""Sentinel sent back in the edit form to indicate the stored key is kept."""


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
    """Return the Fernet master key stored on ``app.state``."""
    return request.app.state.master_key  # type: ignore[no-any-return]


def blank_instance() -> Instance:
    """Return an Instance pre-filled with defaults for the add-form partial."""
    return Instance(
        id=0,
        name="",
        type=InstanceType.radarr,
        url="",
        api_key="",
        enabled=True,
        batch_size=DEFAULT_BATCH_SIZE,
        sleep_interval_mins=DEFAULT_SLEEP_INTERVAL_MINUTES,
        hourly_cap=DEFAULT_HOURLY_CAP,
        cooldown_days=DEFAULT_COOLDOWN_DAYS,
        post_release_grace_hrs=DEFAULT_POST_RELEASE_GRACE_HOURS,
        queue_limit=DEFAULT_QUEUE_LIMIT,
        cutoff_enabled=False,
        cutoff_batch_size=DEFAULT_CUTOFF_BATCH_SIZE,
        cutoff_cooldown_days=DEFAULT_CUTOFF_COOLDOWN_DAYS,
        cutoff_hourly_cap=DEFAULT_CUTOFF_HOURLY_CAP,
        sonarr_search_mode=SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE),
        lidarr_search_mode=LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE),
        readarr_search_mode=ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE),
        whisparr_v2_search_mode=WhisparrV2SearchMode(DEFAULT_WHISPARR_V2_SEARCH_MODE),
        created_at="",
        updated_at="",
        allowed_time_window=DEFAULT_ALLOWED_TIME_WINDOW,
        search_order=SearchOrder(DEFAULT_SEARCH_ORDER),
    )


_CLIENT_CONSTRUCTORS: dict[InstanceType, type[ArrClient]] = {
    InstanceType.radarr: RadarrClient,
    InstanceType.sonarr: SonarrClient,
    InstanceType.lidarr: LidarrClient,
    InstanceType.readarr: ReadarrClient,
    InstanceType.whisparr_v2: WhisparrV2Client,
    InstanceType.whisparr_v3: WhisparrV3Client,
}


def build_client(instance_type: InstanceType, url: str, api_key: str) -> ArrClient:
    """Construct the *arr client matching *instance_type*."""
    client_cls = _CLIENT_CONSTRUCTORS.get(instance_type)
    if client_cls is None:
        msg = f"No client for instance type: {instance_type!r}"
        raise ValueError(msg)
    return client_cls(url=url, api_key=api_key)


async def check_connection(
    instance_type: InstanceType,
    url: str,
    api_key: str,
) -> ConnectionCheck:
    """Test connectivity and identify the remote *arr application.

    Kept here (and not in :mod:`houndarr.services.instance_validation`)
    because it performs a live HTTP probe through the client layer;
    the validation service stays pure.  The returned
    :class:`ConnectionCheck` is the service type so callers only see
    one dataclass for the connection-probe result.
    """
    client = build_client(instance_type, url, api_key)
    async with client:
        status = await client.ping()
    if status is None:
        return ConnectionCheck(reachable=False)
    return ConnectionCheck(
        reachable=True,
        app_name=status.app_name,
        version=status.version,
    )


def connection_status_response(message: str, ok: bool, status_code: int) -> HTMLResponse:
    """Render the inline connection-test status snippet for HTMX swap."""
    trigger = "houndarr-connection-test-success" if ok else "houndarr-connection-test-failure"
    color = "text-green-400" if ok else "text-red-400"
    return HTMLResponse(
        content=f'<span class="text-xs {color}">{html.escape(message)}</span>',
        status_code=status_code,
        headers={"HX-Trigger": trigger},
    )


def connection_guard_response(message: str) -> HTMLResponse:
    """Re-target an error to the connection status span when a save is blocked."""
    return HTMLResponse(
        content=f'<span class="text-xs text-red-400">{html.escape(message)}</span>',
        status_code=422,
        headers={
            "HX-Retarget": "#instance-connection-status",
            "HX-Reswap": "innerHTML",
            "HX-Trigger": "houndarr-connection-test-failure",
        },
    )


async def render_settings_page(
    request: Request,
    *,
    status_code: int = 200,
    account_error: str | None = None,
    account_success: str | None = None,
) -> HTMLResponse:
    """Render the settings page with common account and instance context."""
    from houndarr.database import get_setting

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

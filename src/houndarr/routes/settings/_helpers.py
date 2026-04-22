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
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from houndarr import __version__
from houndarr.auth import resolve_signed_in_as
from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrClient
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
    DEFAULT_WHISPARR_SEARCH_MODE,
    get_settings,
)
from houndarr.routes._htmx import is_hx_request
from houndarr.services.instances import (
    Instance,
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SearchOrder,
    SonarrSearchMode,
    WhisparrSearchMode,
    active_error_instance_ids,
    list_instances,
)

logger = logging.getLogger(__name__)

API_KEY_UNCHANGED = "__UNCHANGED__"
"""Sentinel sent back in the edit form to indicate the stored key is kept."""

_templates: Jinja2Templates | None = None


def get_templates() -> Jinja2Templates:
    """Return the lazy Jinja2Templates singleton for the settings routes."""
    global _templates  # noqa: PLW0603
    if _templates is None:
        # This module lives at src/houndarr/routes/settings/_helpers.py.
        # Templates live at src/houndarr/templates.  Three parents reach
        # the houndarr package root regardless of where the package is
        # installed.
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        _templates = Jinja2Templates(directory=str(templates_dir))
    return _templates


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
        whisparr_search_mode=WhisparrSearchMode(DEFAULT_WHISPARR_SEARCH_MODE),
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
    InstanceType.whisparr_v2: WhisparrClient,
    InstanceType.whisparr_v3: WhisparrV3Client,
}


def build_client(instance_type: InstanceType, url: str, api_key: str) -> ArrClient:
    """Construct the *arr client matching *instance_type*."""
    client_cls = _CLIENT_CONSTRUCTORS.get(instance_type)
    if client_cls is None:
        msg = f"No client for instance type: {instance_type!r}"
        raise ValueError(msg)
    return client_cls(url=url, api_key=api_key)


@dataclass(frozen=True, slots=True)
class ConnectionCheck:
    """Result of a connection test against an *arr instance."""

    reachable: bool
    app_name: str | None = None
    version: str | None = None


_APP_NAME_TO_TYPE: dict[str, InstanceType] = {
    "radarr": InstanceType.radarr,
    "sonarr": InstanceType.sonarr,
    "lidarr": InstanceType.lidarr,
    "readarr": InstanceType.readarr,
    # Whisparr v2 and v3 both report appName "Whisparr"; version-based
    # disambiguation is handled in type_mismatch_message.
    "whisparr": InstanceType.whisparr_v2,
}


async def check_connection(
    instance_type: InstanceType,
    url: str,
    api_key: str,
) -> ConnectionCheck:
    """Test connectivity and identify the remote *arr application."""
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


def _whisparr_version_major(version: str | None) -> int | None:
    """Extract the major version number from a Whisparr version string."""
    if not version:
        return None
    try:
        return int(version.split(".")[0])
    except (ValueError, IndexError):
        return None


def type_mismatch_message(check: ConnectionCheck, selected: InstanceType) -> str | None:
    """Return a mismatch error string, or ``None`` if the type is valid."""
    if check.app_name is None:
        return None

    app_lower = check.app_name.lower()
    detected = _APP_NAME_TO_TYPE.get(app_lower)

    # Whisparr v2 and v3 both report appName "Whisparr". Use version to
    # detect v3 (major version >= 3) and check against the selected type.
    if app_lower == "whisparr":
        major = _whisparr_version_major(check.version)
        if major is not None and major >= 3 and selected == InstanceType.whisparr_v2:
            return (
                f"Version mismatch: this URL runs Whisparr v3 ({check.version})."
                " Select 'Whisparr v3' as the instance type."
            )
        if major is not None and major < 3 and selected == InstanceType.whisparr_v3:
            return (
                f"Version mismatch: this URL runs Whisparr v2 ({check.version})."
                " Select 'Whisparr v2' as the instance type."
            )
        # Correct pairing; skip the generic app-name check.
        return None

    if detected is None:
        # Unknown app name (e.g. a Readarr fork); allow through.
        return None
    if detected != selected:
        return f"Type mismatch: this URL is running {check.app_name}, not {selected.value.title()}."
    return None


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


def validate_cutoff_controls(
    cutoff_batch_size: int,
    cutoff_cooldown_days: int,
    cutoff_hourly_cap: int,
) -> str | None:
    """Validate cutoff-specific numeric controls from form submissions."""
    if cutoff_batch_size < 1:
        return "Cutoff batch size must be at least 1."
    if cutoff_cooldown_days < 0:
        return "Cutoff cooldown days must be 0 or greater."
    if cutoff_hourly_cap < 0:
        return "Cutoff hourly cap must be 0 or greater."
    return None


def validate_upgrade_controls(
    upgrade_batch_size: int,
    upgrade_cooldown_days: int,
    upgrade_hourly_cap: int,
) -> str | None:
    """Validate upgrade-specific numeric controls from form submissions."""
    if upgrade_batch_size < 1:
        return "Upgrade batch size must be at least 1."
    if upgrade_cooldown_days < 7:
        return "Upgrade cooldown days must be at least 7."
    if upgrade_hourly_cap < 0:
        return "Upgrade hourly cap must be 0 or greater."
    return None


class SearchModes:
    """Resolved per-app search mode enum values."""

    __slots__ = ("lidarr", "readarr", "sonarr", "whisparr")

    def __init__(
        self,
        sonarr: SonarrSearchMode,
        lidarr: LidarrSearchMode,
        readarr: ReadarrSearchMode,
        whisparr: WhisparrSearchMode,
    ) -> None:
        self.sonarr = sonarr
        self.lidarr = lidarr
        self.readarr = readarr
        self.whisparr = whisparr


def resolve_search_modes(
    instance_type: InstanceType,
    sonarr_raw: str,
    lidarr_raw: str,
    readarr_raw: str,
    whisparr_raw: str,
) -> SearchModes | str:
    """Validate and resolve per-app search mode strings into enum values.

    Returns a :class:`SearchModes` with validated values, or a plain error
    string if any value is invalid.  Non-applicable search modes default to
    their enum's first value.
    """
    try:
        sonarr_mode = (
            SonarrSearchMode(sonarr_raw)
            if instance_type == InstanceType.sonarr
            else SonarrSearchMode.episode
        )
    except ValueError:
        return "Invalid Sonarr search mode."

    try:
        lidarr_mode = (
            LidarrSearchMode(lidarr_raw)
            if instance_type == InstanceType.lidarr
            else LidarrSearchMode.album
        )
    except ValueError:
        return "Invalid Lidarr search mode."

    try:
        readarr_mode = (
            ReadarrSearchMode(readarr_raw)
            if instance_type == InstanceType.readarr
            else ReadarrSearchMode.book
        )
    except ValueError:
        return "Invalid Readarr search mode."

    try:
        whisparr_mode = (
            WhisparrSearchMode(whisparr_raw)
            if instance_type == InstanceType.whisparr_v2
            else WhisparrSearchMode.episode
        )
    except ValueError:
        return "Invalid Whisparr search mode."

    return SearchModes(
        sonarr=sonarr_mode,
        lidarr=lidarr_mode,
        readarr=readarr_mode,
        whisparr=whisparr_mode,
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

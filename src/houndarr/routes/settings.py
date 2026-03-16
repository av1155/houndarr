"""Settings page routes — instance management via HTMX."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from houndarr import __version__
from houndarr.auth import check_password, get_username, set_password
from houndarr.clients.base import ArrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_BATCH_SIZE,
    DEFAULT_CUTOFF_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_HOURLY_CAP,
    DEFAULT_HOURLY_CAP,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_SONARR_SEARCH_MODE,
    DEFAULT_UNRELEASED_DELAY_HOURS,
)
from houndarr.engine.supervisor import Supervisor
from houndarr.services.instances import (
    Instance,
    InstanceType,
    SonarrSearchMode,
    create_instance,
    delete_instance,
    get_instance,
    list_instances,
    update_instance,
)
from houndarr.services.url_validation import validate_instance_url

router = APIRouter()

# Sentinel value used in the edit form API key field to indicate "no change".
# The actual key is never sent back to the browser; the form pre-fills this
# placeholder so users know a key is already stored.  On save, if the
# submitted value equals this sentinel, the existing encrypted key is kept.
_API_KEY_UNCHANGED = "__UNCHANGED__"

_templates: Jinja2Templates | None = None


def _is_hx_request(request: Request) -> bool:
    """Return True when request is an HTMX request."""
    return request.headers.get("HX-Request") == "true"


def _get_templates() -> Jinja2Templates:
    global _templates  # noqa: PLW0603
    if _templates is None:
        _templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
    return _templates


def _render(
    request: Request,
    template_name: str,
    status_code: int = 200,
    **ctx: object,
) -> HTMLResponse:
    from houndarr.auth import CSRF_COOKIE_NAME

    csrf_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    context = {"version": __version__, "csrf_token": csrf_token, **ctx}
    return _get_templates().TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
    )


def _master_key(request: Request) -> bytes:
    return request.app.state.master_key  # type: ignore[no-any-return]


def _blank_instance() -> Instance:
    return Instance(
        id=0,
        name="",
        type=InstanceType.sonarr,
        url="",
        api_key="",
        enabled=True,
        batch_size=DEFAULT_BATCH_SIZE,
        sleep_interval_mins=DEFAULT_SLEEP_INTERVAL_MINUTES,
        hourly_cap=DEFAULT_HOURLY_CAP,
        cooldown_days=DEFAULT_COOLDOWN_DAYS,
        unreleased_delay_hrs=DEFAULT_UNRELEASED_DELAY_HOURS,
        cutoff_enabled=False,
        cutoff_batch_size=DEFAULT_CUTOFF_BATCH_SIZE,
        cutoff_cooldown_days=DEFAULT_CUTOFF_COOLDOWN_DAYS,
        cutoff_hourly_cap=DEFAULT_CUTOFF_HOURLY_CAP,
        sonarr_search_mode=SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE),
        created_at="",
        updated_at="",
    )


def _build_client(instance_type: InstanceType, url: str, api_key: str) -> ArrClient:
    if instance_type == InstanceType.sonarr:
        return SonarrClient(url=url, api_key=api_key)
    return RadarrClient(url=url, api_key=api_key)


async def _connection_ok(instance_type: InstanceType, url: str, api_key: str) -> bool:
    client = _build_client(instance_type, url, api_key)
    async with client:
        return await client.ping()


def _connection_status_response(message: str, ok: bool, status_code: int) -> HTMLResponse:
    trigger = "houndarr-connection-test-success" if ok else "houndarr-connection-test-failure"
    color = "text-green-400" if ok else "text-red-400"
    return HTMLResponse(
        content=f'<span class="text-xs {color}">{html.escape(message)}</span>',
        status_code=status_code,
        headers={"HX-Trigger": trigger},
    )


def _connection_guard_response(message: str) -> HTMLResponse:
    return HTMLResponse(
        content=f'<span class="text-xs text-red-400">{html.escape(message)}</span>',
        status_code=422,
        headers={
            "HX-Retarget": "#instance-connection-status",
            "HX-Reswap": "innerHTML",
            "HX-Trigger": "houndarr-connection-test-failure",
        },
    )


def _validate_cutoff_controls(
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


# ---------------------------------------------------------------------------
# Settings index
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request) -> HTMLResponse:
    """Render the settings page with the current list of instances."""
    return await _render_settings_page(request)


async def _render_settings_page(
    request: Request,
    *,
    status_code: int = 200,
    account_error: str | None = None,
    account_success: str | None = None,
) -> HTMLResponse:
    """Render settings page with common account and instance context."""
    instances = await list_instances(master_key=_master_key(request))
    username = await get_username()
    template_name = (
        "partials/pages/settings_content.html" if _is_hx_request(request) else "settings.html"
    )
    return _render(
        request,
        template_name,
        status_code=status_code,
        instances=instances,
        username=username,
        account_error=account_error,
        account_success=account_success,
    )


@router.post("/settings/account/password", response_class=HTMLResponse)
async def account_password_update(
    request: Request,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    new_password_confirm: Annotated[str, Form()],
) -> HTMLResponse:
    """Update admin password from the settings page."""
    if not await check_password(current_password):
        return await _render_settings_page(
            request,
            status_code=422,
            account_error="Current password is incorrect.",
        )

    if len(new_password) < 8:
        return await _render_settings_page(
            request,
            status_code=422,
            account_error="New password must be at least 8 characters.",
        )

    if new_password != new_password_confirm:
        return await _render_settings_page(
            request,
            status_code=422,
            account_error="New passwords do not match.",
        )

    if new_password == current_password:
        return await _render_settings_page(
            request,
            status_code=422,
            account_error="New password must be different from current password.",
        )

    await set_password(new_password)
    return await _render_settings_page(
        request,
        account_success="Password updated successfully.",
    )


# ---------------------------------------------------------------------------
# Add-form partial (injected into the add-instance modal)
# ---------------------------------------------------------------------------


@router.get("/settings/instances/add-form", response_class=HTMLResponse)
async def instance_add_form(request: Request) -> HTMLResponse:
    """Return the blank add-instance form partial for HTMX modal injection."""
    return _render(
        request, "partials/instance_form.html", instance=_blank_instance(), editing=False
    )


@router.post("/settings/instances/test-connection", response_class=HTMLResponse)
async def instance_test_connection(
    request: Request,
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    instance_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Test Sonarr/Radarr connectivity and return a status snippet.

    When testing from the edit form, ``api_key`` may be the unchanged sentinel
    value (``__UNCHANGED__``).  In that case the existing stored key is
    retrieved from the database and used for the connection test.
    """
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return _connection_status_response(
            "Invalid type. Must be Sonarr or Radarr.",
            ok=False,
            status_code=422,
        )

    url_error = validate_instance_url(url)
    if url_error is not None:
        return _connection_status_response(url_error, ok=False, status_code=422)

    resolved_api_key = api_key
    if api_key == _API_KEY_UNCHANGED and instance_id:
        try:
            iid = int(instance_id)
        except ValueError:
            return _connection_status_response(
                "Invalid instance ID for key lookup.",
                ok=False,
                status_code=422,
            )
        existing = await get_instance(iid, master_key=_master_key(request))
        if existing is None:
            return _connection_status_response(
                "Instance not found.",
                ok=False,
                status_code=404,
            )
        resolved_api_key = existing.api_key

    ok = await _connection_ok(instance_type, url.rstrip("/"), resolved_api_key)
    if ok:
        return _connection_status_response(
            "Connection successful.",
            ok=True,
            status_code=200,
        )
    return _connection_status_response(
        "Connection failed. Check URL/API key and try again.",
        ok=False,
        status_code=422,
    )


# ---------------------------------------------------------------------------
# Create instance
# ---------------------------------------------------------------------------


@router.post("/settings/instances", response_class=HTMLResponse)
async def instance_create(
    request: Request,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    batch_size: Annotated[int, Form()] = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: Annotated[int, Form()] = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: Annotated[int, Form()] = DEFAULT_HOURLY_CAP,
    cooldown_days: Annotated[int, Form()] = DEFAULT_COOLDOWN_DAYS,
    unreleased_delay_hrs: Annotated[int, Form()] = DEFAULT_UNRELEASED_DELAY_HOURS,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: Annotated[int, Form()] = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: Annotated[int, Form()] = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: Annotated[str, Form()] = DEFAULT_SONARR_SEARCH_MODE,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Create a new instance and return the updated instance table body."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return _connection_guard_response("Invalid instance type.")

    url_error = validate_instance_url(url)
    if url_error is not None:
        return _connection_guard_response(url_error)

    validation_error = _validate_cutoff_controls(
        cutoff_batch_size,
        cutoff_cooldown_days,
        cutoff_hourly_cap,
    )
    if validation_error is not None:
        return _connection_guard_response(validation_error)

    if connection_verified != "true":
        return _connection_guard_response("Test connection successfully before adding.")

    if not await _connection_ok(instance_type, url.rstrip("/"), api_key):
        return _connection_guard_response("Connection test failed. Re-test before adding.")

    if instance_type == InstanceType.sonarr:
        try:
            sonarr_mode = SonarrSearchMode(sonarr_search_mode)
        except ValueError:
            return _connection_guard_response("Invalid Sonarr search mode.")
    else:
        sonarr_mode = SonarrSearchMode.episode

    instance = await create_instance(
        master_key=_master_key(request),
        name=name,
        type=instance_type,
        url=url.rstrip("/"),
        api_key=api_key,
        enabled=True,
        batch_size=batch_size,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=cutoff_enabled == "on",
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        sonarr_search_mode=sonarr_mode,
    )

    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.reconcile_instance(instance.id)

    instances = await list_instances(master_key=_master_key(request))
    # HTMX: return just the refreshed table body partial
    return _render(request, "partials/instance_table_body.html", instances=instances)


# ---------------------------------------------------------------------------
# Edit form partial
# ---------------------------------------------------------------------------


@router.get("/settings/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit_get(request: Request, instance_id: int) -> HTMLResponse:
    """Return the edit form partial for an existing instance."""
    instance = await get_instance(instance_id, master_key=_master_key(request))
    if instance is None:
        return HTMLResponse(content="Not found", status_code=404)
    return _render(request, "partials/instance_form.html", instance=instance, editing=True)


# ---------------------------------------------------------------------------
# Update instance
# ---------------------------------------------------------------------------


@router.post("/settings/instances/{instance_id}", response_class=HTMLResponse)
async def instance_update(
    request: Request,
    instance_id: int,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    batch_size: Annotated[int, Form()] = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: Annotated[int, Form()] = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: Annotated[int, Form()] = DEFAULT_HOURLY_CAP,
    cooldown_days: Annotated[int, Form()] = DEFAULT_COOLDOWN_DAYS,
    unreleased_delay_hrs: Annotated[int, Form()] = DEFAULT_UNRELEASED_DELAY_HOURS,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: Annotated[int, Form()] = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: Annotated[int, Form()] = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: Annotated[str, Form()] = DEFAULT_SONARR_SEARCH_MODE,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Update an existing instance and return the refreshed row partial.

    The ``api_key`` field may contain the unchanged sentinel value
    (``__UNCHANGED__``) when the operator has not modified the key.  In that
    case the existing encrypted key is preserved; otherwise the new key is
    encrypted and stored.
    """
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return _connection_guard_response("Invalid instance type.")

    url_error = validate_instance_url(url)
    if url_error is not None:
        return _connection_guard_response(url_error)

    validation_error = _validate_cutoff_controls(
        cutoff_batch_size,
        cutoff_cooldown_days,
        cutoff_hourly_cap,
    )
    if validation_error is not None:
        return _connection_guard_response(validation_error)

    # Fetch the current instance early; needed for both key resolution and save
    current = await get_instance(instance_id, master_key=_master_key(request))
    if current is None:
        return HTMLResponse(content="Not found", status_code=404)

    # Resolve the actual API key to use (sentinel → keep existing)
    resolved_api_key = current.api_key if api_key == _API_KEY_UNCHANGED else api_key

    if connection_verified != "true":
        return _connection_guard_response("Test connection successfully before saving changes.")

    if not await _connection_ok(instance_type, url.rstrip("/"), resolved_api_key):
        return _connection_guard_response("Connection test failed. Re-test before saving changes.")

    if instance_type == InstanceType.sonarr:
        try:
            sonarr_mode = SonarrSearchMode(sonarr_search_mode)
        except ValueError:
            return _connection_guard_response("Invalid Sonarr search mode.")
    else:
        sonarr_mode = SonarrSearchMode.episode

    updated = await update_instance(
        instance_id,
        master_key=_master_key(request),
        name=name,
        type=instance_type,
        url=url.rstrip("/"),
        api_key=resolved_api_key,
        enabled=current.enabled,
        batch_size=batch_size,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=cutoff_enabled == "on",
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        sonarr_search_mode=sonarr_mode,
    )
    if updated is None:
        return HTMLResponse(content="Not found", status_code=404)

    # HTMX: return just the refreshed row
    return _render(request, "partials/instance_row.html", instance=updated)


# ---------------------------------------------------------------------------
# Delete instance
# ---------------------------------------------------------------------------


@router.delete("/settings/instances/{instance_id}")
async def instance_delete(request: Request, instance_id: int) -> Response:
    """Delete an instance; returns empty 200 so HTMX removes the row."""
    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.stop_instance_task(instance_id)

    await delete_instance(instance_id)
    # Return an empty 200 — HTMX hx-swap="outerHTML" with empty content
    # removes the row from the DOM.
    return Response(status_code=200)


@router.post("/settings/instances/{instance_id}/toggle-enabled", response_class=HTMLResponse)
async def instance_toggle_enabled(request: Request, instance_id: int) -> HTMLResponse:
    """Toggle enabled state for an instance and return refreshed row partial."""
    instance = await get_instance(instance_id, master_key=_master_key(request))
    if instance is None:
        return HTMLResponse(content="Not found", status_code=404)

    updated = await update_instance(
        instance_id,
        master_key=_master_key(request),
        name=instance.name,
        type=instance.type,
        url=instance.url,
        api_key=instance.api_key,
        enabled=not instance.enabled,
        batch_size=instance.batch_size,
        sleep_interval_mins=instance.sleep_interval_mins,
        hourly_cap=instance.hourly_cap,
        cooldown_days=instance.cooldown_days,
        unreleased_delay_hrs=instance.unreleased_delay_hrs,
        cutoff_enabled=instance.cutoff_enabled,
        cutoff_batch_size=instance.cutoff_batch_size,
        cutoff_cooldown_days=instance.cutoff_cooldown_days,
        cutoff_hourly_cap=instance.cutoff_hourly_cap,
        sonarr_search_mode=instance.sonarr_search_mode,
    )
    if updated is None:
        return HTMLResponse(content="Not found", status_code=404)

    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.reconcile_instance(updated.id)

    return _render(request, "partials/instance_row.html", instance=updated)

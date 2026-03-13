"""Settings page routes — instance management via HTMX."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from houndarr import __version__
from houndarr.clients.base import ArrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_BATCH_SIZE,
    DEFAULT_HOURLY_CAP,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_UNRELEASED_DELAY_HOURS,
)
from houndarr.services.instances import (
    Instance,
    InstanceType,
    create_instance,
    delete_instance,
    get_instance,
    list_instances,
    update_instance,
)

router = APIRouter()

_templates: Jinja2Templates | None = None


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
    context = {"version": __version__, **ctx}
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
        content=f'<span class="text-xs {color}">{message}</span>',
        status_code=status_code,
        headers={"HX-Trigger": trigger},
    )


def _connection_guard_response(message: str) -> HTMLResponse:
    return HTMLResponse(
        content=f'<span class="text-xs text-red-400">{message}</span>',
        status_code=422,
        headers={
            "HX-Retarget": "#instance-connection-status",
            "HX-Reswap": "innerHTML",
            "HX-Trigger": "houndarr-connection-test-failure",
        },
    )


# ---------------------------------------------------------------------------
# Settings index
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request) -> HTMLResponse:
    """Render the settings page with the current list of instances."""
    instances = await list_instances(master_key=_master_key(request))
    return _render(request, "settings.html", instances=instances)


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
) -> HTMLResponse:
    """Test Sonarr/Radarr connectivity and return a status snippet."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return _connection_status_response(
            "Invalid type. Must be Sonarr or Radarr.",
            ok=False,
            status_code=422,
        )

    ok = await _connection_ok(instance_type, url.rstrip("/"), api_key)
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
    enabled: Annotated[str, Form()] = "on",
    batch_size: Annotated[int, Form()] = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: Annotated[int, Form()] = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: Annotated[int, Form()] = DEFAULT_HOURLY_CAP,
    cooldown_days: Annotated[int, Form()] = DEFAULT_COOLDOWN_DAYS,
    unreleased_delay_hrs: Annotated[int, Form()] = DEFAULT_UNRELEASED_DELAY_HOURS,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Create a new instance and return the updated instance table body."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return _connection_guard_response("Invalid instance type.")

    if connection_verified != "true":
        return _connection_guard_response("Test connection successfully before adding.")

    if not await _connection_ok(instance_type, url.rstrip("/"), api_key):
        return _connection_guard_response("Connection test failed. Re-test before adding.")

    await create_instance(
        master_key=_master_key(request),
        name=name,
        type=instance_type,
        url=url.rstrip("/"),
        api_key=api_key,
        enabled=enabled == "on",
        batch_size=batch_size,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=cutoff_enabled == "on",
        cutoff_batch_size=cutoff_batch_size,
    )
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
    enabled: Annotated[str, Form()] = "on",
    batch_size: Annotated[int, Form()] = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: Annotated[int, Form()] = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: Annotated[int, Form()] = DEFAULT_HOURLY_CAP,
    cooldown_days: Annotated[int, Form()] = DEFAULT_COOLDOWN_DAYS,
    unreleased_delay_hrs: Annotated[int, Form()] = DEFAULT_UNRELEASED_DELAY_HOURS,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Update an existing instance and return the refreshed row partial."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return _connection_guard_response("Invalid instance type.")

    if connection_verified != "true":
        return _connection_guard_response("Test connection successfully before saving changes.")

    if not await _connection_ok(instance_type, url.rstrip("/"), api_key):
        return _connection_guard_response("Connection test failed. Re-test before saving changes.")

    updated = await update_instance(
        instance_id,
        master_key=_master_key(request),
        name=name,
        type=instance_type,
        url=url.rstrip("/"),
        api_key=api_key,
        enabled=enabled == "on",
        batch_size=batch_size,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=cutoff_enabled == "on",
        cutoff_batch_size=cutoff_batch_size,
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
    )
    if updated is None:
        return HTMLResponse(content="Not found", status_code=404)

    return _render(request, "partials/instance_row.html", instance=updated)

"""Settings page routes — instance management via HTMX."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from houndarr import __version__
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


# ---------------------------------------------------------------------------
# Settings index
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request) -> HTMLResponse:
    """Render the settings page with the current list of instances."""
    instances = await list_instances(master_key=_master_key(request))
    return _render(request, "settings.html", instances=instances)


# ---------------------------------------------------------------------------
# Add-form partial (injected into the slot below the table)
# ---------------------------------------------------------------------------


@router.get("/settings/instances/add-form", response_class=HTMLResponse)
async def instance_add_form(request: Request) -> HTMLResponse:
    """Return the blank add-instance form partial for HTMX injection."""
    # Pass a dummy Instance-like object with defaults so the template can
    # reference instance.* without conditionals everywhere.
    from houndarr.services.instances import Instance, InstanceType

    blank: Instance = Instance(
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
    return _render(request, "partials/instance_form.html", instance=blank, editing=False)


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
) -> HTMLResponse:
    """Create a new instance and return the updated instance table body."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        instances = await list_instances(master_key=_master_key(request))
        return _render(
            request,
            "settings.html",
            status_code=422,
            instances=instances,
            error=f"Invalid instance type: {type!r}. Must be 'sonarr' or 'radarr'.",
        )

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
) -> HTMLResponse:
    """Update an existing instance and return the refreshed row partial."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        instance = await get_instance(instance_id, master_key=_master_key(request))
        if instance is None:
            return HTMLResponse(content="Not found", status_code=404)
        return _render(
            request,
            "partials/instance_form.html",
            status_code=422,
            instance=instance,
            editing=True,
            error=f"Invalid type: {type!r}.",
        )

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

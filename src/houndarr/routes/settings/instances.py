"""Instance CRUD routes under /settings/instances/*.

Covers the add-form modal partial, the test-connection probe, create,
edit-form partial, update, delete, and the enable/disable toggle.
Every mutating route reuses the validation helpers and connection
check from :mod:`houndarr.routes.settings._helpers`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response

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
    DEFAULT_UPGRADE_BATCH_SIZE,
    DEFAULT_UPGRADE_COOLDOWN_DAYS,
    DEFAULT_UPGRADE_HOURLY_CAP,
    DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    DEFAULT_UPGRADE_SERIES_WINDOW_SIZE,
    DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE,
    DEFAULT_WHISPARR_V2_SEARCH_MODE,
)
from houndarr.deps import get_master_key
from houndarr.engine.supervisor import Supervisor
from houndarr.errors import InstanceValidationError
from houndarr.routes.settings._helpers import (
    blank_instance,
    connection_guard_response,
    connection_status_response,
    render,
)
from houndarr.services.instance_submit import (
    InstanceNotFoundError,
    submit_create,
    submit_update,
)
from houndarr.services.instance_validation import run_connection_test
from houndarr.services.instances import (
    active_error_instance_ids,
    delete_instance,
    get_instance,
    list_instances,
    update_instance,
)
from houndarr.services.metrics import invalidate_dashboard_cache

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/settings/instances/add-form", response_class=HTMLResponse)
async def instance_add_form(request: Request) -> HTMLResponse:
    """Return the blank add-instance form partial for HTMX modal injection."""
    blank = blank_instance()
    return render(
        request,
        "partials/instance_form.html",
        instance=blank,
        defaults=blank,
        editing=False,
    )


@router.post("/settings/instances/test-connection", response_class=HTMLResponse)
async def instance_test_connection(
    master_key: Annotated[bytes, Depends(get_master_key)],
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    instance_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Test *arr instance connectivity and return a status snippet.

    The route is a thin dispatch over
    :func:`houndarr.services.instance_validation.run_connection_test`;
    the service owns the sentinel-resolution, SSRF gate, live probe,
    type-mismatch check, and message shaping.  When ``api_key`` is the
    edit-form sentinel and ``instance_id`` is set, the service looks
    up the stored key before the probe.
    """
    outcome = await run_connection_test(
        master_key=master_key,
        type_value=type,
        url=url,
        api_key=api_key,
        instance_id=instance_id,
    )
    return connection_status_response(
        outcome.message, ok=outcome.ok, status_code=outcome.status_code
    )


@router.post("/settings/instances", response_class=HTMLResponse)
async def instance_create(
    request: Request,
    master_key: Annotated[bytes, Depends(get_master_key)],
    name: Annotated[str, Form()],
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    batch_size: Annotated[int, Form()] = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: Annotated[int, Form()] = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: Annotated[int, Form()] = DEFAULT_HOURLY_CAP,
    cooldown_days: Annotated[int, Form()] = DEFAULT_COOLDOWN_DAYS,
    post_release_grace_hrs: Annotated[int, Form()] = DEFAULT_POST_RELEASE_GRACE_HOURS,
    queue_limit: Annotated[int, Form()] = DEFAULT_QUEUE_LIMIT,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: Annotated[int, Form()] = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: Annotated[int, Form()] = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: Annotated[str, Form()] = DEFAULT_SONARR_SEARCH_MODE,
    lidarr_search_mode: Annotated[str, Form()] = DEFAULT_LIDARR_SEARCH_MODE,
    readarr_search_mode: Annotated[str, Form()] = DEFAULT_READARR_SEARCH_MODE,
    whisparr_v2_search_mode: Annotated[str, Form()] = DEFAULT_WHISPARR_V2_SEARCH_MODE,
    upgrade_enabled: Annotated[str, Form()] = "",
    upgrade_batch_size: Annotated[int, Form()] = DEFAULT_UPGRADE_BATCH_SIZE,
    upgrade_cooldown_days: Annotated[int, Form()] = DEFAULT_UPGRADE_COOLDOWN_DAYS,
    upgrade_hourly_cap: Annotated[int, Form()] = DEFAULT_UPGRADE_HOURLY_CAP,
    upgrade_sonarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    upgrade_lidarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    upgrade_readarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    upgrade_whisparr_v2_search_mode: Annotated[str, Form()] = (
        DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE
    ),
    upgrade_series_window_size: Annotated[int, Form()] = DEFAULT_UPGRADE_SERIES_WINDOW_SIZE,
    allowed_time_window: Annotated[str, Form()] = DEFAULT_ALLOWED_TIME_WINDOW,
    search_order: Annotated[str, Form()] = DEFAULT_SEARCH_ORDER,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Create a new instance and return the updated instance table body."""
    try:
        instance = await submit_create(
            master_key=master_key,
            name=name,
            type=type,
            url=url,
            api_key=api_key,
            batch_size=batch_size,
            sleep_interval_mins=sleep_interval_mins,
            hourly_cap=hourly_cap,
            cooldown_days=cooldown_days,
            post_release_grace_hrs=post_release_grace_hrs,
            queue_limit=queue_limit,
            cutoff_enabled=cutoff_enabled == "on",
            cutoff_batch_size=cutoff_batch_size,
            cutoff_cooldown_days=cutoff_cooldown_days,
            cutoff_hourly_cap=cutoff_hourly_cap,
            sonarr_search_mode=sonarr_search_mode,
            lidarr_search_mode=lidarr_search_mode,
            readarr_search_mode=readarr_search_mode,
            whisparr_v2_search_mode=whisparr_v2_search_mode,
            upgrade_enabled=upgrade_enabled == "on",
            upgrade_batch_size=upgrade_batch_size,
            upgrade_cooldown_days=upgrade_cooldown_days,
            upgrade_hourly_cap=upgrade_hourly_cap,
            upgrade_sonarr_search_mode=upgrade_sonarr_search_mode,
            upgrade_lidarr_search_mode=upgrade_lidarr_search_mode,
            upgrade_readarr_search_mode=upgrade_readarr_search_mode,
            upgrade_whisparr_v2_search_mode=upgrade_whisparr_v2_search_mode,
            upgrade_series_window_size=upgrade_series_window_size,
            allowed_time_window=allowed_time_window,
            search_order=search_order,
            connection_verified=connection_verified == "true",
        )
    except InstanceValidationError as exc:
        return connection_guard_response(exc.public_message)

    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.reconcile_instance(instance.core.id)

    invalidate_dashboard_cache(request.app.state)

    instances = await list_instances(master_key=master_key)
    error_ids = await active_error_instance_ids()
    # HTMX: return just the refreshed table body partial
    return render(
        request,
        "partials/instance_table_body.html",
        instances=instances,
        active_error_ids=error_ids,
    )


@router.get("/settings/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit_get(
    request: Request,
    master_key: Annotated[bytes, Depends(get_master_key)],
    instance_id: int,
) -> HTMLResponse:
    """Return the edit form partial for an existing instance."""
    instance = await get_instance(instance_id, master_key=master_key)
    if instance is None:
        return HTMLResponse(content="Not found", status_code=404)
    return render(
        request,
        "partials/instance_form.html",
        instance=instance,
        defaults=blank_instance(),
        editing=True,
    )


@router.post("/settings/instances/{instance_id}", response_class=HTMLResponse)
async def instance_update(
    request: Request,
    master_key: Annotated[bytes, Depends(get_master_key)],
    instance_id: int,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    batch_size: Annotated[int, Form()] = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: Annotated[int, Form()] = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: Annotated[int, Form()] = DEFAULT_HOURLY_CAP,
    cooldown_days: Annotated[int, Form()] = DEFAULT_COOLDOWN_DAYS,
    post_release_grace_hrs: Annotated[int, Form()] = DEFAULT_POST_RELEASE_GRACE_HOURS,
    queue_limit: Annotated[int, Form()] = DEFAULT_QUEUE_LIMIT,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: Annotated[int, Form()] = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: Annotated[int, Form()] = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: Annotated[str, Form()] = DEFAULT_SONARR_SEARCH_MODE,
    lidarr_search_mode: Annotated[str, Form()] = DEFAULT_LIDARR_SEARCH_MODE,
    readarr_search_mode: Annotated[str, Form()] = DEFAULT_READARR_SEARCH_MODE,
    whisparr_v2_search_mode: Annotated[str, Form()] = DEFAULT_WHISPARR_V2_SEARCH_MODE,
    upgrade_enabled: Annotated[str, Form()] = "",
    upgrade_batch_size: Annotated[int, Form()] = DEFAULT_UPGRADE_BATCH_SIZE,
    upgrade_cooldown_days: Annotated[int, Form()] = DEFAULT_UPGRADE_COOLDOWN_DAYS,
    upgrade_hourly_cap: Annotated[int, Form()] = DEFAULT_UPGRADE_HOURLY_CAP,
    upgrade_sonarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    upgrade_lidarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    upgrade_readarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    upgrade_whisparr_v2_search_mode: Annotated[str, Form()] = (
        DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE
    ),
    upgrade_series_window_size: Annotated[int, Form()] = DEFAULT_UPGRADE_SERIES_WINDOW_SIZE,
    allowed_time_window: Annotated[str, Form()] = DEFAULT_ALLOWED_TIME_WINDOW,
    search_order: Annotated[str, Form()] = DEFAULT_SEARCH_ORDER,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Update an existing instance and return the refreshed row partial.

    The ``api_key`` field may contain the unchanged sentinel value
    (``__UNCHANGED__``) when the operator has not modified the key.  In that
    case the existing encrypted key is preserved; otherwise the new key is
    encrypted and stored.
    """
    try:
        updated = await submit_update(
            instance_id,
            master_key=master_key,
            name=name,
            type=type,
            url=url,
            api_key=api_key,
            batch_size=batch_size,
            sleep_interval_mins=sleep_interval_mins,
            hourly_cap=hourly_cap,
            cooldown_days=cooldown_days,
            post_release_grace_hrs=post_release_grace_hrs,
            queue_limit=queue_limit,
            cutoff_enabled=cutoff_enabled == "on",
            cutoff_batch_size=cutoff_batch_size,
            cutoff_cooldown_days=cutoff_cooldown_days,
            cutoff_hourly_cap=cutoff_hourly_cap,
            sonarr_search_mode=sonarr_search_mode,
            lidarr_search_mode=lidarr_search_mode,
            readarr_search_mode=readarr_search_mode,
            whisparr_v2_search_mode=whisparr_v2_search_mode,
            upgrade_enabled=upgrade_enabled == "on",
            upgrade_batch_size=upgrade_batch_size,
            upgrade_cooldown_days=upgrade_cooldown_days,
            upgrade_hourly_cap=upgrade_hourly_cap,
            upgrade_sonarr_search_mode=upgrade_sonarr_search_mode,
            upgrade_lidarr_search_mode=upgrade_lidarr_search_mode,
            upgrade_readarr_search_mode=upgrade_readarr_search_mode,
            upgrade_whisparr_v2_search_mode=upgrade_whisparr_v2_search_mode,
            upgrade_series_window_size=upgrade_series_window_size,
            allowed_time_window=allowed_time_window,
            search_order=search_order,
            connection_verified=connection_verified == "true",
        )
    except InstanceNotFoundError:
        return HTMLResponse(content="Not found", status_code=404)
    except InstanceValidationError as exc:
        return connection_guard_response(exc.public_message)

    invalidate_dashboard_cache(request.app.state)

    # HTMX: return just the refreshed row
    error_ids = await active_error_instance_ids()
    return render(
        request,
        "partials/instance_row.html",
        instance=updated,
        active_error_ids=error_ids,
    )


@router.delete("/settings/instances/{instance_id}")
async def instance_delete(request: Request, instance_id: int) -> Response:
    """Delete an instance; returns empty 200 so HTMX removes the row."""
    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.stop_instance_task(instance_id)

    await delete_instance(instance_id)
    invalidate_dashboard_cache(request.app.state)
    # Return an empty 200. HTMX hx-swap="outerHTML" with empty content
    # removes the row from the DOM.
    return Response(status_code=200)


@router.post("/settings/instances/{instance_id}/toggle-enabled", response_class=HTMLResponse)
async def instance_toggle_enabled(
    request: Request,
    master_key: Annotated[bytes, Depends(get_master_key)],
    instance_id: int,
) -> HTMLResponse:
    """Toggle enabled state for an instance and return the refreshed row partial.

    The partial update relies on :func:`update_instance`'s ``**fields``
    contract: only the non-``None`` keyword argument lands on the SQL
    write, so every other column (url, api_key, all the policy
    fields) is untouched.  That also skips the Fernet re-encryption
    round trip the pre-D.24 pass-through caused on every toggle.
    """
    instance = await get_instance(instance_id, master_key=master_key)
    if instance is None:
        return HTMLResponse(content="Not found", status_code=404)

    updated = await update_instance(
        instance_id,
        master_key=master_key,
        enabled=not instance.core.enabled,
    )
    if updated is None:
        return HTMLResponse(content="Not found", status_code=404)

    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.reconcile_instance(updated.core.id)

    invalidate_dashboard_cache(request.app.state)

    error_ids = await active_error_instance_ids()
    return render(
        request,
        "partials/instance_row.html",
        instance=updated,
        active_error_ids=error_ids,
    )

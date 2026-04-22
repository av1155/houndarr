"""Instance CRUD routes under /settings/instances/*.

Covers the add-form modal partial, the test-connection probe, create,
edit-form partial, update, delete, and the enable/disable toggle.
Every mutating route reuses the validation helpers and connection
check from :mod:`houndarr.routes.settings._helpers`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
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
    DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE,
    DEFAULT_WHISPARR_SEARCH_MODE,
)
from houndarr.engine.supervisor import Supervisor
from houndarr.routes.settings._helpers import (
    API_KEY_UNCHANGED,
    active_error_instance_ids,
    blank_instance,
    check_connection,
    connection_guard_response,
    connection_status_response,
    master_key,
    render,
    resolve_search_modes,
    type_mismatch_message,
    validate_cutoff_controls,
    validate_upgrade_controls,
)
from houndarr.services.instances import (
    InstanceType,
    SearchOrder,
    create_instance,
    delete_instance,
    get_instance,
    list_instances,
    update_instance,
)
from houndarr.services.time_window import (
    format_ranges,
    parse_time_window,
    validate_allowed_time_window,
)
from houndarr.services.url_validation import validate_instance_url

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
    request: Request,
    type: Annotated[str, Form()],  # noqa: A002
    url: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    instance_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Test *arr instance connectivity and return a status snippet.

    When testing from the edit form, ``api_key`` may be the unchanged sentinel
    value (``__UNCHANGED__``).  In that case the existing stored key is
    retrieved from the database and used for the connection test.
    """
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return connection_status_response(
            "Invalid instance type.",
            ok=False,
            status_code=422,
        )

    url_error = validate_instance_url(url)
    if url_error is not None:
        return connection_status_response(url_error, ok=False, status_code=422)

    resolved_api_key = api_key
    if api_key == API_KEY_UNCHANGED and instance_id:
        try:
            iid = int(instance_id)
        except ValueError:
            return connection_status_response(
                "Invalid instance ID for key lookup.",
                ok=False,
                status_code=422,
            )
        existing = await get_instance(iid, master_key=master_key(request))
        if existing is None:
            return connection_status_response(
                "Instance not found.",
                ok=False,
                status_code=404,
            )
        resolved_api_key = existing.api_key

    check = await check_connection(instance_type, url.rstrip("/"), resolved_api_key)
    if not check.reachable:
        return connection_status_response(
            "Connection failed. Check URL/API key and try again.",
            ok=False,
            status_code=422,
        )

    mismatch = type_mismatch_message(check, instance_type)
    if mismatch is not None:
        return connection_status_response(mismatch, ok=False, status_code=422)

    action = "save changes" if instance_id else "add this instance"
    if check.app_name and check.version:
        msg = f"Connected to {check.app_name} v{check.version}. You can now {action}."
    elif check.app_name:
        msg = f"Connected to {check.app_name}. You can now {action}."
    else:
        msg = f"Connection successful. You can now {action}."
    return connection_status_response(msg, ok=True, status_code=200)


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
    post_release_grace_hrs: Annotated[int, Form()] = DEFAULT_POST_RELEASE_GRACE_HOURS,
    queue_limit: Annotated[int, Form()] = DEFAULT_QUEUE_LIMIT,
    cutoff_enabled: Annotated[str, Form()] = "",
    cutoff_batch_size: Annotated[int, Form()] = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: Annotated[int, Form()] = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: Annotated[int, Form()] = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: Annotated[str, Form()] = DEFAULT_SONARR_SEARCH_MODE,
    lidarr_search_mode: Annotated[str, Form()] = DEFAULT_LIDARR_SEARCH_MODE,
    readarr_search_mode: Annotated[str, Form()] = DEFAULT_READARR_SEARCH_MODE,
    whisparr_search_mode: Annotated[str, Form()] = DEFAULT_WHISPARR_SEARCH_MODE,
    upgrade_enabled: Annotated[str, Form()] = "",
    upgrade_batch_size: Annotated[int, Form()] = DEFAULT_UPGRADE_BATCH_SIZE,
    upgrade_cooldown_days: Annotated[int, Form()] = DEFAULT_UPGRADE_COOLDOWN_DAYS,
    upgrade_hourly_cap: Annotated[int, Form()] = DEFAULT_UPGRADE_HOURLY_CAP,
    upgrade_sonarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    upgrade_lidarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    upgrade_readarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    upgrade_whisparr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE,
    allowed_time_window: Annotated[str, Form()] = DEFAULT_ALLOWED_TIME_WINDOW,
    search_order: Annotated[str, Form()] = DEFAULT_SEARCH_ORDER,
    connection_verified: Annotated[str, Form()] = "false",
) -> HTMLResponse:
    """Create a new instance and return the updated instance table body."""
    try:
        instance_type = InstanceType(type)
    except ValueError:
        return connection_guard_response("Invalid instance type.")

    url_error = validate_instance_url(url)
    if url_error is not None:
        return connection_guard_response(url_error)

    window_error = validate_allowed_time_window(allowed_time_window)
    if window_error is not None:
        return connection_guard_response(window_error)
    canonical_window = format_ranges(parse_time_window(allowed_time_window))

    validation_error = validate_cutoff_controls(
        cutoff_batch_size,
        cutoff_cooldown_days,
        cutoff_hourly_cap,
    )
    if validation_error is not None:
        return connection_guard_response(validation_error)

    upgrade_validation_error = validate_upgrade_controls(
        upgrade_batch_size,
        upgrade_cooldown_days,
        upgrade_hourly_cap,
    )
    if upgrade_validation_error is not None:
        return connection_guard_response(upgrade_validation_error)

    if connection_verified != "true":
        return connection_guard_response("Test connection successfully before adding.")

    create_check = await check_connection(instance_type, url.rstrip("/"), api_key)
    if not create_check.reachable:
        return connection_guard_response("Connection test failed. Re-test before adding.")
    create_mismatch = type_mismatch_message(create_check, instance_type)
    if create_mismatch is not None:
        return connection_guard_response(create_mismatch)

    search_modes = resolve_search_modes(
        instance_type,
        sonarr_search_mode,
        lidarr_search_mode,
        readarr_search_mode,
        whisparr_search_mode,
    )
    if isinstance(search_modes, str):
        return connection_guard_response(search_modes)

    upgrade_modes = resolve_search_modes(
        instance_type,
        upgrade_sonarr_search_mode,
        upgrade_lidarr_search_mode,
        upgrade_readarr_search_mode,
        upgrade_whisparr_search_mode,
    )
    if isinstance(upgrade_modes, str):
        return connection_guard_response(upgrade_modes)

    try:
        parsed_search_order = SearchOrder(search_order)
    except ValueError:
        return connection_guard_response("Invalid search order.")

    instance = await create_instance(
        master_key=master_key(request),
        name=name,
        type=instance_type,
        url=url.rstrip("/"),
        api_key=api_key,
        enabled=True,
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
        sonarr_search_mode=search_modes.sonarr,
        lidarr_search_mode=search_modes.lidarr,
        readarr_search_mode=search_modes.readarr,
        whisparr_search_mode=search_modes.whisparr,
        upgrade_enabled=upgrade_enabled == "on",
        upgrade_batch_size=upgrade_batch_size,
        upgrade_cooldown_days=upgrade_cooldown_days,
        upgrade_hourly_cap=upgrade_hourly_cap,
        upgrade_sonarr_search_mode=upgrade_modes.sonarr,
        upgrade_lidarr_search_mode=upgrade_modes.lidarr,
        upgrade_readarr_search_mode=upgrade_modes.readarr,
        upgrade_whisparr_search_mode=upgrade_modes.whisparr,
        allowed_time_window=canonical_window,
        search_order=parsed_search_order,
    )

    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.reconcile_instance(instance.id)

    instances = await list_instances(master_key=master_key(request))
    error_ids = await active_error_instance_ids()
    # HTMX: return just the refreshed table body partial
    return render(
        request,
        "partials/instance_table_body.html",
        instances=instances,
        active_error_ids=error_ids,
    )


@router.get("/settings/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit_get(request: Request, instance_id: int) -> HTMLResponse:
    """Return the edit form partial for an existing instance."""
    instance = await get_instance(instance_id, master_key=master_key(request))
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
    whisparr_search_mode: Annotated[str, Form()] = DEFAULT_WHISPARR_SEARCH_MODE,
    upgrade_enabled: Annotated[str, Form()] = "",
    upgrade_batch_size: Annotated[int, Form()] = DEFAULT_UPGRADE_BATCH_SIZE,
    upgrade_cooldown_days: Annotated[int, Form()] = DEFAULT_UPGRADE_COOLDOWN_DAYS,
    upgrade_hourly_cap: Annotated[int, Form()] = DEFAULT_UPGRADE_HOURLY_CAP,
    upgrade_sonarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    upgrade_lidarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    upgrade_readarr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    upgrade_whisparr_search_mode: Annotated[str, Form()] = DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE,
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
        instance_type = InstanceType(type)
    except ValueError:
        return connection_guard_response("Invalid instance type.")

    url_error = validate_instance_url(url)
    if url_error is not None:
        return connection_guard_response(url_error)

    window_error = validate_allowed_time_window(allowed_time_window)
    if window_error is not None:
        return connection_guard_response(window_error)
    canonical_window = format_ranges(parse_time_window(allowed_time_window))

    validation_error = validate_cutoff_controls(
        cutoff_batch_size,
        cutoff_cooldown_days,
        cutoff_hourly_cap,
    )
    if validation_error is not None:
        return connection_guard_response(validation_error)

    upgrade_validation_error = validate_upgrade_controls(
        upgrade_batch_size,
        upgrade_cooldown_days,
        upgrade_hourly_cap,
    )
    if upgrade_validation_error is not None:
        return connection_guard_response(upgrade_validation_error)

    # Fetch the current instance early; needed for both key resolution and save
    current = await get_instance(instance_id, master_key=master_key(request))
    if current is None:
        return HTMLResponse(content="Not found", status_code=404)

    # Resolve the actual API key to use (sentinel → keep existing)
    resolved_api_key = current.api_key if api_key == API_KEY_UNCHANGED else api_key

    if connection_verified != "true":
        return connection_guard_response("Test connection successfully before saving changes.")

    update_check = await check_connection(instance_type, url.rstrip("/"), resolved_api_key)
    if not update_check.reachable:
        return connection_guard_response("Connection test failed. Re-test before saving changes.")
    update_mismatch = type_mismatch_message(update_check, instance_type)
    if update_mismatch is not None:
        return connection_guard_response(update_mismatch)

    search_modes = resolve_search_modes(
        instance_type,
        sonarr_search_mode,
        lidarr_search_mode,
        readarr_search_mode,
        whisparr_search_mode,
    )
    if isinstance(search_modes, str):
        return connection_guard_response(search_modes)

    upgrade_modes = resolve_search_modes(
        instance_type,
        upgrade_sonarr_search_mode,
        upgrade_lidarr_search_mode,
        upgrade_readarr_search_mode,
        upgrade_whisparr_search_mode,
    )
    if isinstance(upgrade_modes, str):
        return connection_guard_response(upgrade_modes)

    try:
        parsed_search_order = SearchOrder(search_order)
    except ValueError:
        return connection_guard_response("Invalid search order.")

    # Reset offsets when upgrade is toggled off
    new_upgrade_enabled = upgrade_enabled == "on"
    upgrade_fields: dict[str, object] = {
        "upgrade_enabled": new_upgrade_enabled,
        "upgrade_batch_size": upgrade_batch_size,
        "upgrade_cooldown_days": upgrade_cooldown_days,
        "upgrade_hourly_cap": upgrade_hourly_cap,
        "upgrade_sonarr_search_mode": upgrade_modes.sonarr,
        "upgrade_lidarr_search_mode": upgrade_modes.lidarr,
        "upgrade_readarr_search_mode": upgrade_modes.readarr,
        "upgrade_whisparr_search_mode": upgrade_modes.whisparr,
    }
    if current.upgrade_enabled and not new_upgrade_enabled:
        upgrade_fields["upgrade_item_offset"] = 0
        upgrade_fields["upgrade_series_offset"] = 0

    updated = await update_instance(
        instance_id,
        master_key=master_key(request),
        name=name,
        type=instance_type,
        url=url.rstrip("/"),
        api_key=resolved_api_key,
        enabled=current.enabled,
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
        sonarr_search_mode=search_modes.sonarr,
        lidarr_search_mode=search_modes.lidarr,
        readarr_search_mode=search_modes.readarr,
        whisparr_search_mode=search_modes.whisparr,
        missing_page_offset=1,
        cutoff_page_offset=1,
        allowed_time_window=canonical_window,
        search_order=parsed_search_order,
        **upgrade_fields,
    )
    if updated is None:
        return HTMLResponse(content="Not found", status_code=404)

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
    # Return an empty 200. HTMX hx-swap="outerHTML" with empty content
    # removes the row from the DOM.
    return Response(status_code=200)


@router.post("/settings/instances/{instance_id}/toggle-enabled", response_class=HTMLResponse)
async def instance_toggle_enabled(request: Request, instance_id: int) -> HTMLResponse:
    """Toggle enabled state for an instance and return refreshed row partial."""
    instance = await get_instance(instance_id, master_key=master_key(request))
    if instance is None:
        return HTMLResponse(content="Not found", status_code=404)

    updated = await update_instance(
        instance_id,
        master_key=master_key(request),
        name=instance.name,
        type=instance.type,
        url=instance.url,
        api_key=instance.api_key,
        enabled=not instance.enabled,
        batch_size=instance.batch_size,
        sleep_interval_mins=instance.sleep_interval_mins,
        hourly_cap=instance.hourly_cap,
        cooldown_days=instance.cooldown_days,
        post_release_grace_hrs=instance.post_release_grace_hrs,
        queue_limit=instance.queue_limit,
        cutoff_enabled=instance.cutoff_enabled,
        cutoff_batch_size=instance.cutoff_batch_size,
        cutoff_cooldown_days=instance.cutoff_cooldown_days,
        cutoff_hourly_cap=instance.cutoff_hourly_cap,
        sonarr_search_mode=instance.sonarr_search_mode,
        lidarr_search_mode=instance.lidarr_search_mode,
        readarr_search_mode=instance.readarr_search_mode,
        whisparr_search_mode=instance.whisparr_search_mode,
    )
    if updated is None:
        return HTMLResponse(content="Not found", status_code=404)

    supervisor = getattr(request.app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.reconcile_instance(updated.id)

    error_ids = await active_error_instance_ids()
    return render(
        request,
        "partials/instance_row.html",
        instance=updated,
        active_error_ids=error_ids,
    )

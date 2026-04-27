"""Instance submit service: orchestration for create + update routes.

Track D.10 lifts the validation, connection-test, and persistence
orchestration out of :mod:`houndarr.routes.settings.instances` so the
route handlers become pure form-parse-and-render shells.  Each service
function performs the same sequence of steps the route used to:

1. Parse the raw ``type`` string into :class:`InstanceType`.
2. Run the URL / time-window / cutoff-control / upgrade-control
   validators and surface the first failure as
   :class:`~houndarr.errors.InstanceValidationError`.
3. Gate on the ``connection_verified`` flag (the UI requires a
   green "test connection" before save).
4. Run a live connection test via :func:`check_connection` and
   :func:`type_mismatch_message`, refusing to persist when the
   remote app is unreachable or reports the wrong type.
5. Resolve the four per-app ``*_search_mode`` form values into a
   :class:`SearchModeBundle`.
6. Parse the ``search_order`` form value into :class:`SearchOrder`.
7. Persist via :func:`create_instance` / :func:`update_instance` and
   return the resulting :class:`Instance`.

Errors carry a human-readable message that the route renders into
the connection-status guard banner via
:func:`connection_guard_response`.  The service raises rather than
returning a tagged tuple so the caller's happy path stays linear.

Track D.11 will lift the validators out of
:mod:`houndarr.routes.settings._helpers` into a dedicated
:mod:`houndarr.services.instance_validation` module; until then this
service imports them through their current home.
"""

from __future__ import annotations

from houndarr.errors import InstanceValidationError
from houndarr.routes.settings._helpers import API_KEY_UNCHANGED, check_connection
from houndarr.services.instance_validation import (
    SearchModes,
    resolve_search_modes,
    type_mismatch_message,
    validate_cutoff_controls,
    validate_upgrade_controls,
)
from houndarr.services.instances import (
    Instance,
    InstanceType,
    SearchOrder,
    create_instance,
    get_instance,
    update_instance,
)
from houndarr.services.time_window import (
    format_ranges,
    parse_time_window,
    validate_allowed_time_window,
)
from houndarr.services.url_validation import validate_instance_url


class InstanceNotFoundError(InstanceValidationError):
    """Raised by :func:`submit_update` when the target instance id is missing.

    Distinct from generic validation so the route can map it to a 404
    instead of the connection-guard 422.  Inherits from
    :class:`~houndarr.errors.InstanceValidationError` (and therefore
    :class:`~houndarr.errors.ServiceError`) so the broader
    error-handling discipline still applies.
    """


def _parse_type(raw: str) -> InstanceType:
    """Parse the form's raw ``type`` string into :class:`InstanceType`."""
    try:
        return InstanceType(raw)
    except ValueError as exc:
        raise InstanceValidationError("Invalid instance type.") from exc


def _parse_search_order(raw: str) -> SearchOrder:
    """Parse the form's raw ``search_order`` string into :class:`SearchOrder`."""
    try:
        return SearchOrder(raw)
    except ValueError as exc:
        raise InstanceValidationError("Invalid search order.") from exc


def _validate_form(
    *,
    url: str,
    allowed_time_window: str,
    cutoff_batch_size: int,
    cutoff_cooldown_days: int,
    cutoff_hourly_cap: int,
    upgrade_batch_size: int,
    upgrade_cooldown_days: int,
    upgrade_hourly_cap: int,
) -> str:
    """Run every static validator and return the canonical time window.

    Raises:
        InstanceValidationError: First validator failure; message is
            the form-level string the user sees in the guard banner.
    """
    url_error = validate_instance_url(url)
    if url_error is not None:
        raise InstanceValidationError(url_error)

    window_error = validate_allowed_time_window(allowed_time_window)
    if window_error is not None:
        raise InstanceValidationError(window_error)

    cutoff_error = validate_cutoff_controls(
        cutoff_batch_size, cutoff_cooldown_days, cutoff_hourly_cap
    )
    if cutoff_error is not None:
        raise InstanceValidationError(cutoff_error)

    upgrade_error = validate_upgrade_controls(
        upgrade_batch_size, upgrade_cooldown_days, upgrade_hourly_cap
    )
    if upgrade_error is not None:
        raise InstanceValidationError(upgrade_error)

    return format_ranges(parse_time_window(allowed_time_window))


async def _verify_remote(
    instance_type: InstanceType, url: str, api_key: str, *, blocked_message: str
) -> None:
    """Run the live connection test and refuse on unreachable / type mismatch.

    Raises:
        InstanceValidationError: When the remote rejects the request,
            the URL is unreachable, or the remote's app name and
            version do not match *instance_type*.
    """
    check = await check_connection(instance_type, url, api_key)
    if not check.reachable:
        raise InstanceValidationError(blocked_message)
    mismatch = type_mismatch_message(check, instance_type)
    if mismatch is not None:
        raise InstanceValidationError(mismatch)


def _resolve_modes_or_raise(
    instance_type: InstanceType,
    sonarr: str,
    lidarr: str,
    readarr: str,
    whisparr_v2: str,
) -> SearchModes:
    """Resolve the four per-app mode values to enum instances.

    Returns the :class:`SearchModes` bundle the caller can destructure
    field-by-field.  Re-raises any validation error string from
    :func:`resolve_search_modes` as
    :class:`InstanceValidationError` so the route always sees the
    typed surface.
    """
    bundle = resolve_search_modes(instance_type, sonarr, lidarr, readarr, whisparr_v2)
    if isinstance(bundle, str):
        raise InstanceValidationError(bundle)
    return bundle


async def submit_create(
    *,
    master_key: bytes,
    name: str,
    type: str,  # noqa: A002
    url: str,
    api_key: str,
    batch_size: int,
    sleep_interval_mins: int,
    hourly_cap: int,
    cooldown_days: int,
    post_release_grace_hrs: int,
    queue_limit: int,
    cutoff_enabled: bool,
    cutoff_batch_size: int,
    cutoff_cooldown_days: int,
    cutoff_hourly_cap: int,
    sonarr_search_mode: str,
    lidarr_search_mode: str,
    readarr_search_mode: str,
    whisparr_v2_search_mode: str,
    upgrade_enabled: bool,
    upgrade_batch_size: int,
    upgrade_cooldown_days: int,
    upgrade_hourly_cap: int,
    upgrade_sonarr_search_mode: str,
    upgrade_lidarr_search_mode: str,
    upgrade_readarr_search_mode: str,
    upgrade_whisparr_v2_search_mode: str,
    upgrade_series_window_size: int,
    allowed_time_window: str,
    search_order: str,
    connection_verified: bool,
) -> Instance:
    """Validate + persist a new instance, returning the populated row.

    The route layer feeds in the raw form values; the service does
    every check, every coercion, and every connection test before the
    INSERT lands.  ``connection_verified`` reflects the form's hidden
    flag set by the JS-driven test-connection click; the live HTTP
    test then runs again here so the gate cannot be forged by a
    direct POST that flips the flag.

    Args:
        master_key: Fernet key for the resulting INSERT.
        name / type / url / api_key / ...: Raw form fields.  Booleans
            are passed in already converted (the route maps the
            string ``"on"`` to ``True``).
        connection_verified: Whether the UI's test-connection step
            previously succeeded.  Required ``True`` to proceed.

    Returns:
        The created :class:`Instance` with its database-assigned id.

    Raises:
        InstanceValidationError: For any validation, connection-test,
            or type-resolution failure.  ``str(exc)`` is the message
            the route renders into the guard banner.
    """
    instance_type = _parse_type(type)
    canonical_window = _validate_form(
        url=url,
        allowed_time_window=allowed_time_window,
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        upgrade_batch_size=upgrade_batch_size,
        upgrade_cooldown_days=upgrade_cooldown_days,
        upgrade_hourly_cap=upgrade_hourly_cap,
    )

    if not connection_verified:
        raise InstanceValidationError("Test connection successfully before adding.")

    cleaned_url = url.rstrip("/")
    await _verify_remote(
        instance_type,
        cleaned_url,
        api_key,
        blocked_message="Connection test failed. Re-test before adding.",
    )

    modes = _resolve_modes_or_raise(
        instance_type,
        sonarr_search_mode,
        lidarr_search_mode,
        readarr_search_mode,
        whisparr_v2_search_mode,
    )
    upgrade_modes = _resolve_modes_or_raise(
        instance_type,
        upgrade_sonarr_search_mode,
        upgrade_lidarr_search_mode,
        upgrade_readarr_search_mode,
        upgrade_whisparr_v2_search_mode,
    )

    parsed_search_order = _parse_search_order(search_order)

    return await create_instance(
        master_key=master_key,
        name=name,
        type=instance_type,
        url=cleaned_url,
        api_key=api_key,
        enabled=True,
        batch_size=batch_size,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        post_release_grace_hrs=post_release_grace_hrs,
        queue_limit=queue_limit,
        cutoff_enabled=cutoff_enabled,
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        sonarr_search_mode=modes.sonarr,
        lidarr_search_mode=modes.lidarr,
        readarr_search_mode=modes.readarr,
        whisparr_v2_search_mode=modes.whisparr_v2,
        upgrade_enabled=upgrade_enabled,
        upgrade_batch_size=upgrade_batch_size,
        upgrade_cooldown_days=upgrade_cooldown_days,
        upgrade_hourly_cap=upgrade_hourly_cap,
        upgrade_sonarr_search_mode=upgrade_modes.sonarr,
        upgrade_lidarr_search_mode=upgrade_modes.lidarr,
        upgrade_readarr_search_mode=upgrade_modes.readarr,
        upgrade_whisparr_v2_search_mode=upgrade_modes.whisparr_v2,
        upgrade_series_window_size=upgrade_series_window_size,
        allowed_time_window=canonical_window,
        search_order=parsed_search_order,
    )


async def submit_update(
    instance_id: int,
    *,
    master_key: bytes,
    name: str,
    type: str,  # noqa: A002
    url: str,
    api_key: str,
    batch_size: int,
    sleep_interval_mins: int,
    hourly_cap: int,
    cooldown_days: int,
    post_release_grace_hrs: int,
    queue_limit: int,
    cutoff_enabled: bool,
    cutoff_batch_size: int,
    cutoff_cooldown_days: int,
    cutoff_hourly_cap: int,
    sonarr_search_mode: str,
    lidarr_search_mode: str,
    readarr_search_mode: str,
    whisparr_v2_search_mode: str,
    upgrade_enabled: bool,
    upgrade_batch_size: int,
    upgrade_cooldown_days: int,
    upgrade_hourly_cap: int,
    upgrade_sonarr_search_mode: str,
    upgrade_lidarr_search_mode: str,
    upgrade_readarr_search_mode: str,
    upgrade_whisparr_v2_search_mode: str,
    upgrade_series_window_size: int,
    allowed_time_window: str,
    search_order: str,
    connection_verified: bool,
) -> Instance:
    """Validate + persist an instance update, returning the refreshed row.

    Mirrors :func:`submit_create` plus three update-only steps:

    - Look up the current row via :func:`get_instance`; raise
      :class:`InstanceNotFoundError` when the id is unknown so the
      route can render a 404.
    - Resolve the API key sentinel (``API_KEY_UNCHANGED``) by
      substituting the current row's plaintext key when the form
      indicates the operator did not edit it.
    - Reset ``upgrade_item_offset`` and ``upgrade_series_offset`` to
      ``0`` when the upgrade pass is being toggled off, so a future
      re-enable starts from a clean position.

    Args:
        instance_id: Primary key of the row to update.
        master_key: Fernet key for the UPDATE round trip.
        Other args mirror :func:`submit_create`.

    Returns:
        The refreshed :class:`Instance` after persistence.

    Raises:
        InstanceNotFoundError: When *instance_id* does not exist.
        InstanceValidationError: For any validation, connection-test,
            or type-resolution failure.
    """
    current = await get_instance(instance_id, master_key=master_key)
    if current is None:
        raise InstanceNotFoundError("Instance not found.")

    instance_type = _parse_type(type)
    canonical_window = _validate_form(
        url=url,
        allowed_time_window=allowed_time_window,
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        upgrade_batch_size=upgrade_batch_size,
        upgrade_cooldown_days=upgrade_cooldown_days,
        upgrade_hourly_cap=upgrade_hourly_cap,
    )

    resolved_api_key = current.api_key if api_key == API_KEY_UNCHANGED else api_key

    if not connection_verified:
        raise InstanceValidationError("Test connection successfully before saving changes.")

    cleaned_url = url.rstrip("/")
    await _verify_remote(
        instance_type,
        cleaned_url,
        resolved_api_key,
        blocked_message="Connection test failed. Re-test before saving changes.",
    )

    modes = _resolve_modes_or_raise(
        instance_type,
        sonarr_search_mode,
        lidarr_search_mode,
        readarr_search_mode,
        whisparr_v2_search_mode,
    )
    upgrade_modes = _resolve_modes_or_raise(
        instance_type,
        upgrade_sonarr_search_mode,
        upgrade_lidarr_search_mode,
        upgrade_readarr_search_mode,
        upgrade_whisparr_v2_search_mode,
    )

    parsed_search_order = _parse_search_order(search_order)

    update_kwargs: dict[str, object] = {
        "name": name,
        "type": instance_type,
        "url": cleaned_url,
        "api_key": resolved_api_key,
        "enabled": current.enabled,
        "batch_size": batch_size,
        "sleep_interval_mins": sleep_interval_mins,
        "hourly_cap": hourly_cap,
        "cooldown_days": cooldown_days,
        "post_release_grace_hrs": post_release_grace_hrs,
        "queue_limit": queue_limit,
        "cutoff_enabled": cutoff_enabled,
        "cutoff_batch_size": cutoff_batch_size,
        "cutoff_cooldown_days": cutoff_cooldown_days,
        "cutoff_hourly_cap": cutoff_hourly_cap,
        "sonarr_search_mode": modes.sonarr,
        "lidarr_search_mode": modes.lidarr,
        "readarr_search_mode": modes.readarr,
        "whisparr_v2_search_mode": modes.whisparr_v2,
        "upgrade_enabled": upgrade_enabled,
        "upgrade_batch_size": upgrade_batch_size,
        "upgrade_cooldown_days": upgrade_cooldown_days,
        "upgrade_hourly_cap": upgrade_hourly_cap,
        "upgrade_sonarr_search_mode": upgrade_modes.sonarr,
        "upgrade_lidarr_search_mode": upgrade_modes.lidarr,
        "upgrade_readarr_search_mode": upgrade_modes.readarr,
        "upgrade_whisparr_v2_search_mode": upgrade_modes.whisparr_v2,
        "upgrade_series_window_size": upgrade_series_window_size,
        "missing_page_offset": 1,
        "cutoff_page_offset": 1,
        "allowed_time_window": canonical_window,
        "search_order": parsed_search_order,
    }
    # Reset offsets when upgrade is toggled off so a future re-enable
    # starts from a clean position rather than picking up halfway
    # through the page rotation cycle.
    if current.upgrade_enabled and not upgrade_enabled:
        update_kwargs["upgrade_item_offset"] = 0
        update_kwargs["upgrade_series_offset"] = 0

    updated = await update_instance(instance_id, master_key=master_key, **update_kwargs)
    if updated is None:
        # update_instance returns None only if the row vanished between
        # the initial get_instance and the UPDATE; treat as not-found
        # so the route renders the same 404 path it would have for the
        # original lookup miss.
        raise InstanceNotFoundError("Instance not found.")
    return updated

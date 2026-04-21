"""Admin service layer for bulk destructive operations.

Three top-level helpers back the routes under ``/settings/admin``:

* :func:`reset_all_instance_policy`: revert every instance's policy
  columns (batch/sleep/cap/cooldown, cutoff, upgrade, search modes,
  time window, search order) and pagination cursors back to the
  :mod:`houndarr.config` defaults while preserving identity and
  snapshot columns.
* :func:`clear_all_search_logs`: truncate the ``search_log`` table and
  leave a single audit breadcrumb.
* :func:`factory_reset`: stop the supervisor, delete the SQLite
  database and master-key files, re-initialise fresh state, and reset
  the in-memory auth caches. Used by the Danger zone action. If the
  in-process re-init raises after the on-disk wipe, the route layer
  falls back to a delayed process exit so the orchestrator restarts
  the container; on next boot ``init_db`` + ``ensure_master_key``
  resume from the empty data directory exactly as on first run.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI

from houndarr.auth import reset_auth_caches
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
    DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE,
    DEFAULT_WHISPARR_V2_SEARCH_MODE,
)
from houndarr.crypto import ensure_master_key
from houndarr.database import (
    clear_all_search_logs as _db_clear_all_search_logs,
)
from houndarr.database import (
    init_db,
    write_admin_audit,
)
from houndarr.engine.supervisor import Supervisor
from houndarr.services.instances import (
    LidarrSearchMode,
    ReadarrSearchMode,
    SearchOrder,
    SonarrSearchMode,
    WhisparrV2SearchMode,
    list_instances,
    update_instance,
)

logger = logging.getLogger(__name__)


def _policy_defaults() -> dict[str, object]:
    """Return the column -> default-value dict used by a policy reset.

    Keeping this in one place makes the test surface small (one fixture
    covers every field) and guarantees we don't drift from the values
    the Add-instance form shows as its reset-to-defaults targets.
    """
    return {
        "batch_size": DEFAULT_BATCH_SIZE,
        "sleep_interval_mins": DEFAULT_SLEEP_INTERVAL_MINUTES,
        "hourly_cap": DEFAULT_HOURLY_CAP,
        "cooldown_days": DEFAULT_COOLDOWN_DAYS,
        "post_release_grace_hrs": DEFAULT_POST_RELEASE_GRACE_HOURS,
        "queue_limit": DEFAULT_QUEUE_LIMIT,
        "cutoff_enabled": False,
        "cutoff_batch_size": DEFAULT_CUTOFF_BATCH_SIZE,
        "cutoff_cooldown_days": DEFAULT_CUTOFF_COOLDOWN_DAYS,
        "cutoff_hourly_cap": DEFAULT_CUTOFF_HOURLY_CAP,
        "sonarr_search_mode": SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE),
        "lidarr_search_mode": LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE),
        "readarr_search_mode": ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE),
        "whisparr_v2_search_mode": WhisparrV2SearchMode(DEFAULT_WHISPARR_V2_SEARCH_MODE),
        "upgrade_enabled": False,
        "upgrade_batch_size": DEFAULT_UPGRADE_BATCH_SIZE,
        "upgrade_cooldown_days": DEFAULT_UPGRADE_COOLDOWN_DAYS,
        "upgrade_hourly_cap": DEFAULT_UPGRADE_HOURLY_CAP,
        "upgrade_sonarr_search_mode": SonarrSearchMode(DEFAULT_UPGRADE_SONARR_SEARCH_MODE),
        "upgrade_lidarr_search_mode": LidarrSearchMode(DEFAULT_UPGRADE_LIDARR_SEARCH_MODE),
        "upgrade_readarr_search_mode": ReadarrSearchMode(DEFAULT_UPGRADE_READARR_SEARCH_MODE),
        "upgrade_whisparr_v2_search_mode": WhisparrV2SearchMode(
            DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE,
        ),
        "allowed_time_window": DEFAULT_ALLOWED_TIME_WINDOW,
        "search_order": SearchOrder(DEFAULT_SEARCH_ORDER),
        # Pagination + upgrade-pool cursors: reset so the next cycle walks
        # the library from the top under the fresh cadence, which is the
        # user expectation after pressing a big "reset" button.
        "missing_page_offset": 1,
        "cutoff_page_offset": 1,
        "upgrade_item_offset": 0,
        "upgrade_series_offset": 0,
    }


async def reset_all_instance_policy(
    *,
    master_key: bytes,
    supervisor: Supervisor | None,
) -> int:
    """Revert every instance's policy columns to :mod:`houndarr.config` defaults.

    Identity (name, type, url, api_key, enabled), timestamps, and snapshot
    counters are preserved. The supervisor is reconciled per row so the
    running search loop adopts the new cadence on its next wake; the
    search loop re-reads columns each cycle already, so this is mostly a
    belt-and-braces for the upgrade-offset reset.

    Args:
        master_key: Fernet master key used to decrypt / re-encrypt the
            stored api_key round-trip inside ``update_instance``.
        supervisor: Running supervisor from ``app.state`` (may be ``None``
            during tests). When present, ``reconcile_instance`` is called
            once per row after the update.

    Returns:
        Number of instances that were reset.
    """
    defaults = _policy_defaults()
    instances = await list_instances(master_key=master_key)
    for instance in instances:
        await update_instance(instance.id, master_key=master_key, **defaults)
        if supervisor is not None:
            await supervisor.reconcile_instance(instance.id)

    await write_admin_audit(
        f"Policy settings reset to defaults for {len(instances)} instance(s) by admin",
    )
    return len(instances)


async def clear_all_search_logs() -> int:
    """Truncate the ``search_log`` table, leaving a single audit breadcrumb.

    Returns:
        Number of rows that were removed (excluding the breadcrumb).
    """
    removed = await _db_clear_all_search_logs()
    await write_admin_audit(f"Audit log cleared by admin ({removed} rows removed)")
    return removed


async def factory_reset(*, app: FastAPI, data_dir: str) -> None:
    """Wipe Houndarr back to first-run state.

    Stops the running supervisor, deletes the SQLite database files and
    the master-key file under ``data_dir``, re-initialises an empty
    schema, rotates the master key, resets the auth caches, and spins up
    a fresh supervisor with zero instances.

    If any step after the file deletion raises, the exception is
    re-raised so the route layer can fall back to a delayed process
    exit. The orchestrator restarts the container and on next boot
    ``init_db`` + ``ensure_master_key`` resume from the empty data
    directory exactly as on first run, so no sentinel coordination is
    required. On a successful in-process re-init the app is in the same
    state as it is on first boot.

    Args:
        app: The FastAPI application; ``app.state.supervisor`` is replaced
            and ``app.state.master_key`` is rotated.
        data_dir: Directory containing ``houndarr.db*`` and
            ``houndarr.masterkey``. Must match the path the app was
            launched with.
    """
    data_path = Path(data_dir)
    db_path = data_path / "houndarr.db"
    paths_to_delete = [
        db_path,
        db_path.with_suffix(".db-wal"),
        db_path.with_suffix(".db-shm"),
        data_path / "houndarr.masterkey",
    ]
    sentinel_path = data_path / "factory-reset-pending"

    # Stop every background task that touches the database before the
    # on-disk wipe, so none of them race a half-deleted file or wake up
    # on a schema that has not been recreated yet.
    supervisor = getattr(app.state, "supervisor", None)
    if isinstance(supervisor, Supervisor):
        await supervisor.stop()
    app.state.supervisor = None

    retention_task = getattr(app.state, "retention_task", None)
    if retention_task is not None and not retention_task.done():
        retention_task.cancel()
        with suppress(asyncio.CancelledError):
            await retention_task
    app.state.retention_task = None

    # NOTE: we deliberately do NOT null app.state.master_key here. Keeping
    # the old key in memory keeps any inflight request that reads it (e.g.
    # the dashboard poll) from hitting ``decrypt(None)`` during the few
    # hundred ms the DB is being rebuilt. The key is atomically replaced
    # once the new one is ready.

    try:
        for path in paths_to_delete:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
    except Exception:  # noqa: BLE001
        logger.exception("Factory reset: file deletion failed")
        raise

    # From this point the on-disk state is wiped; any failure leaves a
    # sentinel so the container's next boot can finish the reset cleanly.
    try:
        await init_db()
        new_key = ensure_master_key(str(data_path))
        app.state.master_key = new_key
        reset_auth_caches()
        new_supervisor = Supervisor(master_key=new_key)
        await new_supervisor.start()
        app.state.supervisor = new_supervisor
        # Respawn the log-retention loop we cancelled above. Without this,
        # an operator who stays in the same container process after the
        # reset gets unbounded search_log growth until the next restart.
        # Lazy import avoids a circular dependency: houndarr.app imports
        # this module's siblings during router registration.
        from houndarr.app import _periodic_log_retention

        app.state.retention_task = asyncio.create_task(
            _periodic_log_retention(),
            name="log-retention-loop",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Factory reset: in-process re-init failed; writing sentinel for next boot")
        try:
            sentinel_path.write_text("pending\n", encoding="utf-8")
        except OSError:
            logger.exception("Factory reset: could not write sentinel file")
        raise

    # Clean up any stale sentinel from a previous aborted reset.
    with suppress(FileNotFoundError):
        sentinel_path.unlink()


def request_process_exit() -> None:
    """Exit the container so the orchestrator can restart from scratch.

    Indirected so tests can patch this out (and so we don't accidentally
    wire a forced exit into a hot path). Used only when
    :func:`factory_reset` raises and the hybrid fallback kicks in.
    """
    logger.warning("Factory reset fallback: exiting process for orchestrator restart")
    os._exit(0)

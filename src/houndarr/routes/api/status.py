"""Status API: per-instance search metrics and run-now trigger.

GET  /api/status             → JSON list of InstanceStatus objects
POST /api/instances/{id}/run-now → trigger an immediate search cycle (202)
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency shims
# ---------------------------------------------------------------------------


def get_supervisor(request: Request) -> SupervisorProto:
    """Resolve the running supervisor typed as :class:`SupervisorProto`.

    Track B.21 seam.  The concrete instance is still stashed on
    ``app.state.supervisor`` at lifespan startup; this shim narrows
    the route-facing surface to the Protocol shape so route handlers
    only depend on the methods they invoke (``trigger_run_now`` here;
    ``reconcile_instance`` / ``stop_instance_task`` for future
    migrations of ``routes/settings/instances``).

    Raises :class:`HTTPException` with status 503 when the supervisor
    slot is empty (pre-lifespan, during factory reset, or post-stop).
    The runtime isinstance check uses the concrete
    :class:`~houndarr.engine.supervisor.Supervisor` class for the
    positive identity assertion, then widens the return type to the
    Protocol.  Track D.12 will move this shim into a shared
    :mod:`houndarr.deps` module.
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    if not isinstance(supervisor, Supervisor):
        raise HTTPException(status_code=503, detail="Supervisor unavailable")
    return supervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Columns needed from the instances table for the status response.
# Notably excludes encrypted_api_key to avoid Fernet decryption overhead.
_INSTANCE_COLS = (
    "id, name, type, enabled, batch_size, sleep_interval_mins, hourly_cap,"
    " cooldown_days, cutoff_enabled, cutoff_batch_size,"
    " post_release_grace_hrs, queue_limit"
)

_METRICS_SQL = """
SELECT
    instance_id,
    SUM(CASE WHEN action = 'searched' THEN 1 ELSE 0 END)
        AS items_found_total,
    SUM(CASE WHEN action = 'searched'
             AND date(timestamp) = date('now') THEN 1 ELSE 0 END)
        AS searches_today,
    SUM(CASE WHEN action = 'searched'
             AND julianday(timestamp) >= julianday('now', '-1 hour')
             THEN 1 ELSE 0 END)
        AS searches_last_hour,
    SUM(CASE WHEN action = 'searched'
             AND julianday(timestamp) >= julianday('now', '-24 hours')
             THEN 1 ELSE 0 END)
        AS searched_24h,
    SUM(CASE WHEN action = 'skipped'
             AND julianday(timestamp) >= julianday('now', '-24 hours')
             THEN 1 ELSE 0 END)
        AS skipped_24h,
    SUM(CASE WHEN action = 'error'
             AND julianday(timestamp) >= julianday('now', '-24 hours')
             THEN 1 ELSE 0 END)
        AS errors_24h,
    MAX(CASE WHEN action = 'searched' THEN timestamp END)
        AS last_search_at
FROM search_log
WHERE instance_id IN ({placeholders})
GROUP BY instance_id
"""

_LAST_ACTIVITY_SQL = """
SELECT instance_id, action, timestamp
FROM (
    SELECT instance_id, action, timestamp,
           ROW_NUMBER() OVER (
               PARTITION BY instance_id ORDER BY timestamp DESC
           ) AS rn
    FROM search_log
    WHERE instance_id IN ({placeholders})
      AND action IN ('searched', 'skipped', 'error')
)
WHERE rn = 1
"""


async def _all_instance_metrics(
    db: aiosqlite.Connection,
    instance_ids: list[int],
) -> tuple[dict[int, dict[str, Any]], dict[int, tuple[str | None, str | None]]]:
    """Fetch aggregated search metrics and last-activity for all instances.

    Returns:
        A tuple of (metrics_by_id, last_activity_by_id).
    """
    if not instance_ids:
        return {}, {}

    placeholders = ",".join("?" * len(instance_ids))

    # Aggregated counters per instance
    metrics: dict[int, dict[str, Any]] = {}
    async with db.execute(_METRICS_SQL.format(placeholders=placeholders), instance_ids) as cur:
        async for row in cur:
            iid = row["instance_id"]
            metrics[iid] = {
                "items_found_total": int(row["items_found_total"] or 0),
                "searches_today": int(row["searches_today"] or 0),
                "searches_last_hour": int(row["searches_last_hour"] or 0),
                "searched_24h": int(row["searched_24h"] or 0),
                "skipped_24h": int(row["skipped_24h"] or 0),
                "errors_24h": int(row["errors_24h"] or 0),
                "last_search_at": str(row["last_search_at"]) if row["last_search_at"] else None,
            }

    # Most recent activity row per instance
    activity: dict[int, tuple[str | None, str | None]] = {}
    async with db.execute(
        _LAST_ACTIVITY_SQL.format(placeholders=placeholders), instance_ids
    ) as cur:
        async for row in cur:
            activity[row["instance_id"]] = (str(row["action"]), str(row["timestamp"]))

    return metrics, activity


_EMPTY_METRICS: dict[str, Any] = {
    "items_found_total": 0,
    "searches_today": 0,
    "searches_last_hour": 0,
    "searched_24h": 0,
    "skipped_24h": 0,
    "errors_24h": 0,
    "last_search_at": None,
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/status")
async def get_status(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return per-instance status objects for the dashboard."""
    async with get_db() as db:
        # Fetch instance metadata directly (skips Fernet API-key decryption).
        async with db.execute(
            f"SELECT {_INSTANCE_COLS} FROM instances ORDER BY id ASC"  # noqa: S608  # nosec B608
        ) as cur:
            instances = await cur.fetchall()

        if not instances:
            return JSONResponse([])

        instance_ids = [row["id"] for row in instances]
        metrics_map, activity_map = await _all_instance_metrics(db, instance_ids)

    results: list[dict[str, Any]] = []
    for inst in instances:
        iid = inst["id"]
        m = metrics_map.get(iid, _EMPTY_METRICS)
        act_action, act_at = activity_map.get(iid, (None, None))
        results.append(
            {
                "id": iid,
                "name": inst["name"],
                "type": inst["type"],
                "enabled": bool(inst["enabled"]),
                "last_search_at": m["last_search_at"],
                "searches_last_hour": m["searches_last_hour"],
                "searches_today": m["searches_today"],
                "items_found_total": m["items_found_total"],
                "searched_24h": m["searched_24h"],
                "skipped_24h": m["skipped_24h"],
                "errors_24h": m["errors_24h"],
                "last_activity_action": act_action,
                "last_activity_at": act_at,
                "batch_size": inst["batch_size"],
                "sleep_interval_mins": inst["sleep_interval_mins"],
                "hourly_cap": inst["hourly_cap"],
                "cooldown_days": inst["cooldown_days"],
                "cutoff_enabled": bool(inst["cutoff_enabled"]),
                "cutoff_batch_size": inst["cutoff_batch_size"],
                "post_release_grace_hrs": inst["post_release_grace_hrs"],
                "queue_limit": inst["queue_limit"],
            }
        )

    return JSONResponse(results)


@router.post("/api/instances/{instance_id}/run-now", status_code=202)
async def run_now(
    instance_id: int,
    supervisor: Annotated[SupervisorProto, Depends(get_supervisor)],
) -> JSONResponse:
    """Trigger an immediate search cycle for the given enabled instance."""
    status = await supervisor.trigger_run_now(instance_id)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Instance not found")
    if status == "disabled":
        raise HTTPException(status_code=409, detail="Instance is disabled")

    logger.info("run-now accepted for instance id=%d", instance_id)
    return JSONResponse({"status": "accepted", "instance_id": instance_id}, status_code=202)

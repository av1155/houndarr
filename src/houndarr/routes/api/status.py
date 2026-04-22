"""Status API: per-instance search metrics and run-now trigger.

GET  /api/status             -> JSON envelope
                                ``{"instances": [...], "recent_searches": [...]}``
POST /api/instances/{id}/run-now -> trigger an immediate search cycle (202)
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto
from houndarr.services.metrics import (
    EMPTY_METRICS,
    gather_active_errors,
    gather_cooldown_data,
    gather_lifetime_metrics,
    gather_recent_searches,
    gather_window_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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


# Columns needed from the instances table for the status response.
# Notably excludes encrypted_api_key to avoid Fernet decryption overhead.
_INSTANCE_COLS = (
    "id, name, type, enabled, batch_size, sleep_interval_mins, hourly_cap,"
    " cooldown_days, cutoff_enabled, cutoff_batch_size,"
    " cutoff_cooldown_days,"
    " post_release_grace_hrs, queue_limit,"
    " upgrade_enabled, upgrade_cooldown_days,"
    " monitored_total, unreleased_count, snapshot_refreshed_at"
)


@router.get("/api/status")
async def get_status(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return the dashboard status envelope.

    ``{"instances": [...], "recent_searches": [...]}``: each instance
    carries per-card fields (``monitored_total``, ``unreleased_count``,
    ``lifetime_searched``, ``last_dispatch_at``, ``active_error``,
    ``cooldown_breakdown``, ``unlocking_next``, plus the policy fields
    used by the chip row).  ``recent_searches`` is the global last-5
    dispatches over the past 7 days, joined against instances for the
    type-color rendering.
    """
    async with get_db() as db:
        async with db.execute(
            f"SELECT {_INSTANCE_COLS} FROM instances ORDER BY id ASC"  # noqa: S608  # nosec B608
        ) as cur:
            instances = await cur.fetchall()

        if not instances:
            return JSONResponse({"instances": [], "recent_searches": []})

        instance_ids = [row["id"] for row in instances]
        metrics_map, activity_map = await gather_window_metrics(db, instance_ids)
        lifetime_map = await gather_lifetime_metrics(db, instance_ids)
        error_map = await gather_active_errors(db, instance_ids)
        cooldown_map = await gather_cooldown_data(db, list(instances))
        recent = await gather_recent_searches(db, limit=5)

    results: list[dict[str, Any]] = []
    for inst in instances:
        iid = inst["id"]
        m = metrics_map.get(iid, EMPTY_METRICS)
        act_action, act_at = activity_map.get(iid, (None, None))
        lifetime = lifetime_map.get(iid, {"lifetime_searched": 0, "last_dispatch_at": None})
        cooldown = cooldown_map.get(
            iid,
            {
                "cooldown_breakdown": {"missing": 0, "cutoff": 0, "upgrade": 0},
                "unlocking_next": [],
                "cooldown_total": 0,
            },
        )
        results.append(
            {
                "id": iid,
                "name": inst["name"],
                "type": inst["type"],
                "enabled": bool(inst["enabled"]),
                "last_search_at": m["last_search_at"],
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
                "lifetime_searched": lifetime["lifetime_searched"],
                "last_dispatch_at": lifetime["last_dispatch_at"],
                "active_error": error_map.get(iid),
                "cooldown_breakdown": cooldown["cooldown_breakdown"],
                "unlocking_next": cooldown["unlocking_next"],
                "cooldown_total": cooldown["cooldown_total"],
                "monitored_total": int(inst["monitored_total"]),
                "unreleased_count": int(inst["unreleased_count"]),
                "snapshot_refreshed_at": (
                    str(inst["snapshot_refreshed_at"]) if inst["snapshot_refreshed_at"] else None
                ),
                "upgrade_enabled": bool(inst["upgrade_enabled"]),
                "upgrade_cooldown_days": int(inst["upgrade_cooldown_days"]),
            }
        )

    return JSONResponse({"instances": results, "recent_searches": recent})


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

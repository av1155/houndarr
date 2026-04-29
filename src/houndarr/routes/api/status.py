"""Status API: per-instance search metrics and run-now trigger.

GET  /api/status             -> JSON envelope
                                ``{"instances": [...], "recent_searches": [...]}``
POST /api/instances/{id}/run-now -> trigger an immediate search cycle (202)

Route thinning (D.22) moved every SQL fetch and per-instance
serialisation step into :mod:`houndarr.services.metrics`; the GET
handler now opens a connection, delegates to
:func:`houndarr.services.metrics.gather_dashboard_status`, and wraps
the result in a :class:`JSONResponse`.  The POST handler stays as a
thin :class:`~houndarr.protocols.SupervisorProto` dispatcher that
maps the run-now status strings to HTTP status codes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.deps import get_supervisor
from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto
from houndarr.services.metrics import gather_dashboard_status, invalidate_dashboard_cache

logger = logging.getLogger(__name__)

router = APIRouter()

# Run-now's background write lands ~1-2 s after the route returns; the
# initial synchronous invalidate plus a re-clear after this delay covers
# the dashboard's worst poll alignment without wedging the supervisor.
# Tunable but unlikely to need tuning: anything beyond 5 s starts to
# overlap the next 30 s HTMX poll and adds no value.
_RUN_NOW_REINVALIDATE_DELAY_SECONDS = 3.0


async def _reinvalidate_after(delay_seconds: float, app_state: Any) -> None:
    """Sleep then drop the dashboard cache once the run-now write should have landed.

    Best-effort: a process shutdown cancels the task cleanly via the
    asyncio loop teardown.  ``invalidate_dashboard_cache`` is a no-op
    when the cache attribute is missing, so a torn-down ``app.state``
    cannot raise.
    """
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return
    invalidate_dashboard_cache(app_state)


@router.get("/api/status")
async def get_status(request: Request) -> JSONResponse:
    """Return the dashboard status envelope.

    ``{"instances": [...], "recent_searches": [...]}``.  Each instance
    carries per-card fields (``monitored_total``, ``unreleased_count``,
    ``lifetime_searched``, ``last_dispatch_at``, ``last_cycle_end``,
    ``active_error``, ``cooldown_breakdown``, ``unlocking_next``) plus
    the policy fields used by the chip row; ``recent_searches`` is the
    global last-five dispatches over the past seven days, joined
    against instances for the type-colour rendering.  See
    :func:`houndarr.services.metrics.gather_dashboard_status` for the
    per-field contract.

    ``last_cycle_end`` is pulled from the live supervisor's in-memory
    map (populated at the end of every cycle) so the dashboard
    countdown anchors on a signal that advances once per cycle even
    when the cycle is all-LRU-throttled skips.  Missing when the
    supervisor hasn't booted yet or has never completed a cycle for
    the instance; the client falls back to ``last_activity_at`` in
    those cases.
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    cycle_ends: dict[int, str] = (
        supervisor.cycle_end_timestamps() if isinstance(supervisor, Supervisor) else {}
    )
    aggregate_cache = getattr(request.app.state, "aggregate_cache", None)
    async with get_db() as db:
        envelope = await gather_dashboard_status(
            db,
            cycle_ends=cycle_ends,
            aggregate_cache=aggregate_cache,
        )
    return JSONResponse(envelope)


@router.post("/api/instances/{instance_id}/run-now", status_code=202)
async def run_now(
    request: Request,
    instance_id: int,
    supervisor: Annotated[SupervisorProto, Depends(get_supervisor)],
) -> JSONResponse:
    """Trigger an immediate search cycle for the given enabled instance."""
    status = await supervisor.trigger_run_now(instance_id)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Instance not found")
    if status == "disabled":
        raise HTTPException(status_code=409, detail="Instance is disabled")

    # The supervisor's run-now path writes to ``search_log`` on a
    # background task ~1-2 s later.  The synchronous invalidate clears
    # any cached envelope so a poll that lands during the gap fetches
    # fresh; the deferred re-clear (3 s) ensures the row written by the
    # supervisor's background task does not get hidden behind a cache
    # entry warmed in the milliseconds before it landed.  Both calls
    # are no-ops when the cache is disabled or absent.
    invalidate_dashboard_cache(request.app.state)
    asyncio.create_task(
        _reinvalidate_after(_RUN_NOW_REINVALIDATE_DELAY_SECONDS, request.app.state),
        name=f"run-now-reinvalidate-{instance_id}",
    )

    logger.info("run-now accepted for instance id=%d", instance_id)
    return JSONResponse({"status": "accepted", "instance_id": instance_id}, status_code=202)

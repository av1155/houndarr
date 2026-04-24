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

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto
from houndarr.services.metrics import gather_dashboard_status

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
    async with get_db() as db:
        envelope = await gather_dashboard_status(db, cycle_ends=cycle_ends)
    return JSONResponse(envelope)


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

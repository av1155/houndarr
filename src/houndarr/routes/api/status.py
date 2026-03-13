"""Status API — per-instance search metrics and run-now trigger.

GET  /api/status             → JSON list of InstanceStatus objects
POST /api/instances/{id}/run-now → trigger an immediate search cycle (202)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.engine.supervisor import Supervisor
from houndarr.services.instances import list_instances

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _searches_today(instance_id: int) -> int:
    """Count search_log rows with action='searched' for today (UTC date)."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM search_log
            WHERE instance_id = ?
              AND action = 'searched'
              AND date(timestamp) = date('now')
            """,
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _searches_last_hour(instance_id: int) -> int:
    """Count search_log rows with action='searched' in the past 60 minutes."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM search_log
            WHERE instance_id = ?
              AND action = 'searched'
              AND timestamp >= datetime('now', '-1 hour')
            """,
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _items_found_total(instance_id: int) -> int:
    """Total 'searched' rows ever written for this instance."""
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM search_log WHERE instance_id = ? AND action = 'searched'",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _last_search_at(instance_id: int) -> str | None:
    """Timestamp of the most recent 'searched' log row, or None."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT timestamp FROM search_log
            WHERE instance_id = ? AND action = 'searched'
            ORDER BY timestamp DESC LIMIT 1
            """,
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return str(row["timestamp"]) if row else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/status")
async def get_status(request: Request) -> JSONResponse:
    """Return per-instance status objects for the dashboard."""
    master_key: bytes = request.app.state.master_key
    instances = await list_instances(master_key=master_key)

    results: list[dict[str, Any]] = []
    for inst in instances:
        last_at = await _last_search_at(inst.id)
        results.append(
            {
                "id": inst.id,
                "name": inst.name,
                "type": inst.type,
                "enabled": inst.enabled,
                "last_search_at": last_at,
                "searches_last_hour": await _searches_last_hour(inst.id),
                "searches_today": await _searches_today(inst.id),
                "items_found_total": await _items_found_total(inst.id),
            }
        )

    return JSONResponse(results)


@router.post("/api/instances/{instance_id}/run-now", status_code=202)
async def run_now(instance_id: int, request: Request) -> JSONResponse:
    """Trigger an immediate search cycle for the given enabled instance."""
    supervisor = getattr(request.app.state, "supervisor", None)
    if not isinstance(supervisor, Supervisor):
        raise HTTPException(status_code=503, detail="Supervisor unavailable")

    status = await supervisor.trigger_run_now(instance_id)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Instance not found")
    if status == "disabled":
        raise HTTPException(status_code=409, detail="Instance is disabled")

    logger.info("run-now accepted for instance id=%d", instance_id)
    return JSONResponse({"status": "accepted", "instance_id": instance_id}, status_code=202)

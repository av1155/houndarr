"""External widget API route."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.engine.supervisor import Supervisor
from houndarr.services.metrics import gather_cached_searches_7d, gather_dashboard_status
from houndarr.services.widget_metrics import compute_widget_summary

router = APIRouter()


def _generated_at() -> str:
    """Return the current UTC timestamp for the widget envelope."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@router.get("/api/v1/widget")
async def get_widget(request: Request) -> JSONResponse:
    """Return the stable read-only widget summary envelope."""
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
        instances = envelope.get("instances", [])
        instance_ids = [int(row["id"]) for row in instances if "id" in row]
        searches_7d = await gather_cached_searches_7d(
            db,
            instance_ids=instance_ids,
            aggregate_cache=aggregate_cache,
        )

    widget_envelope: dict[str, Any] = {
        "schema": 1,
        "generated_at": _generated_at(),
        "totals": compute_widget_summary(envelope, searches_7d),
    }
    return JSONResponse(widget_envelope)

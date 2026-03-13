"""Logs API — paginated search_log entries with optional filters.

GET /api/logs         → JSON list of log rows (used by tests and external consumers)
GET /api/logs/partial → server-rendered <tbody> HTMX partial (used by the /logs page)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from houndarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_LOG_LIMIT_DEFAULT = 50
_LOG_LIMIT_MAX = 200

# ---------------------------------------------------------------------------
# Template loader (shared lazy singleton)
# ---------------------------------------------------------------------------

_templates: Jinja2Templates | None = None


def _get_templates() -> Jinja2Templates:
    global _templates  # noqa: PLW0603
    if _templates is None:
        _templates = Jinja2Templates(
            directory=str(Path(__file__).parent.parent.parent / "templates")
        )
    return _templates


# ---------------------------------------------------------------------------
# DB query helper
# ---------------------------------------------------------------------------


async def _query_logs(
    instance_id: int | None,
    action: str | None,
    before: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch log rows from search_log with optional filters.

    Args:
        instance_id: Restrict to a specific instance (None = all).
        action: One of ``searched``, ``skipped``, ``error``, ``info`` (None = all).
        before: ISO-8601 timestamp cursor — return rows older than this value.
        limit: Maximum number of rows to return.

    Returns:
        List of dicts with keys: id, instance_id, instance_name, item_id,
        item_type, action, reason, message, timestamp.
    """
    limit = min(max(1, limit), _LOG_LIMIT_MAX)

    conditions: list[str] = []
    params: list[Any] = []

    if instance_id is not None:
        conditions.append("sl.instance_id = ?")
        params.append(instance_id)

    if action is not None:
        conditions.append("sl.action = ?")
        params.append(action)

    if before is not None:
        conditions.append("sl.timestamp < ?")
        params.append(before)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            sl.id,
            sl.instance_id,
            COALESCE(i.name, 'Deleted') AS instance_name,
            sl.item_id,
            sl.item_type,
            sl.action,
            sl.reason,
            sl.message,
            sl.timestamp
        FROM search_log sl
        LEFT JOIN instances i ON i.id = sl.instance_id
        {where_clause}
        ORDER BY sl.timestamp DESC, sl.id DESC
        LIMIT ?
    """  # noqa: S608  # nosec B608
    params.append(limit)

    async with get_db() as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/logs")
async def get_logs(
    instance_id: int | None = Query(default=None),
    action: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=_LOG_LIMIT_DEFAULT, ge=1, le=_LOG_LIMIT_MAX),
) -> JSONResponse:
    """Return paginated log rows as JSON.

    Args:
        instance_id: Filter to a specific instance ID.
        action: Filter by action (``searched``, ``skipped``, ``error``, ``info``).
        before: Timestamp cursor — only return rows older than this ISO-8601 value.
        limit: Max rows (1–200, default 50).

    Returns:
        JSON array of log-row objects.
    """
    rows = await _query_logs(instance_id, action, before, limit)
    return JSONResponse(rows)


@router.get("/api/logs/partial", response_class=HTMLResponse)
async def get_logs_partial(
    request: Request,
    instance_id: int | None = Query(default=None),
    action: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=_LOG_LIMIT_DEFAULT, ge=1, le=_LOG_LIMIT_MAX),
) -> HTMLResponse:
    """Return a server-rendered <tbody> partial for HTMX swaps.

    Args:
        request: FastAPI request (required for template rendering).
        instance_id: Filter to a specific instance ID.
        action: Filter by action.
        before: Timestamp cursor.
        limit: Max rows.

    Returns:
        HTML fragment containing ``<tbody>`` rows.
    """
    rows = await _query_logs(instance_id, action, before, limit)

    return _get_templates().TemplateResponse(
        request=request,
        name="partials/log_rows.html",
        context={
            "rows": rows,
            # Pass back current filter values so the partial can render pagination
            "instance_id": instance_id,
            "action": action,
            "before": before,
            "limit": limit,
        },
    )

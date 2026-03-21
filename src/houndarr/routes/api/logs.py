"""Logs API: paginated search_log entries with optional filters.

GET /api/logs         → JSON list of log rows (used by tests and external consumers)
GET /api/logs/partial → server-rendered <tbody> HTMX partial (used by the /logs page)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from houndarr.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_LOG_LIMIT_DEFAULT = 50
_LOG_LIMIT_MAX = 5000
_LOG_LIMIT_ALL = 5000
_LOG_LOAD_MORE_CHUNK_MAX = 100
_SEARCH_KINDS = {"missing", "cutoff", "upgrade"}
_CYCLE_TRIGGERS = {"scheduled", "run_now", "system"}


def _compute_load_more_limit(limit: int) -> int:
    """Return a bounded per-request chunk size for load-more pagination."""
    bounded_limit = min(max(1, limit), _LOG_LIMIT_MAX)
    return min(bounded_limit, _LOG_LOAD_MORE_CHUNK_MAX)


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Build compact summary counts for currently visible log rows."""
    searched_rows = 0
    skipped_rows = 0
    error_rows = 0
    info_rows = 0

    cycle_outcomes: dict[str, str] = {}
    for row in rows:
        action = str(row.get("action") or "")
        if action == "searched":
            searched_rows += 1
        elif action == "skipped":
            skipped_rows += 1
        elif action == "error":
            error_rows += 1
        elif action == "info":
            info_rows += 1

        cycle_id = row.get("cycle_id")
        if cycle_id is None:
            continue

        cycle_id_str = str(cycle_id)
        cycle_progress = str(row.get("cycle_progress") or "")
        existing = cycle_outcomes.get(cycle_id_str)
        if existing == "progress" or cycle_progress == "progress":
            cycle_outcomes[cycle_id_str] = "progress"
        elif existing is None:
            cycle_outcomes[cycle_id_str] = cycle_progress or "no_progress"

    searched_cycles = sum(1 for value in cycle_outcomes.values() if value == "progress")
    skip_only_cycles = sum(1 for value in cycle_outcomes.values() if value == "no_progress")

    return {
        "total_rows": len(rows),
        "searched_rows": searched_rows,
        "skipped_rows": skipped_rows,
        "error_rows": error_rows,
        "info_rows": info_rows,
        "total_cycles": len(cycle_outcomes),
        "searched_cycles": searched_cycles,
        "skip_only_cycles": skip_only_cycles,
    }


def _parse_instance_id(raw: str | None) -> int | None:
    """Parse optional instance_id query param from HTMX form values.

    Empty-string values are treated as no filter.

    Args:
        raw: Query param value from request.

    Returns:
        Parsed integer instance ID, or ``None``.

    Raises:
        HTTPException: If a non-empty value is not an integer.
    """
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive path
        raise HTTPException(status_code=422, detail="instance_id must be an integer") from exc


def _parse_search_kind(raw: str | None) -> str | None:
    """Parse optional search_kind query param."""
    if raw is None or raw == "":
        return None
    if raw not in _SEARCH_KINDS:
        raise HTTPException(status_code=422, detail="search_kind must be 'missing' or 'cutoff'")
    return raw


def _parse_cycle_trigger(raw: str | None) -> str | None:
    """Parse optional cycle_trigger query param."""
    if raw is None or raw == "":
        return None
    if raw not in _CYCLE_TRIGGERS:
        raise HTTPException(
            status_code=422,
            detail="cycle_trigger must be 'scheduled', 'run_now', or 'system'",
        )
    return raw


def _parse_hide_system(raw: str | None) -> bool:
    """Parse hide_system checkbox/select values from query params."""
    if raw is None or raw == "":
        return False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=422, detail="hide_system must be a boolean")


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
    search_kind: str | None,
    cycle_trigger: str | None,
    hide_system: bool,
    before: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch log rows from search_log with optional filters.

    Args:
        instance_id: Restrict to a specific instance (None = all).
        action: One of ``searched``, ``skipped``, ``error``, ``info`` (None = all).
        search_kind: ``missing`` or ``cutoff`` (None = all).
        cycle_trigger: ``scheduled``, ``run_now``, or ``system`` (None = all).
        hide_system: When True, hide system lifecycle rows from results.
        before: ISO-8601 timestamp cursor; returns rows older than this value.
        limit: Maximum number of rows to return.

    Returns:
        List of dicts with keys: id, instance_id, instance_name, item_id,
        item_type, search_kind, cycle_id, cycle_trigger, item_label, action,
        reason, message, timestamp.
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

    if search_kind is not None:
        conditions.append("sl.search_kind = ?")
        params.append(search_kind)

    if cycle_trigger is not None:
        conditions.append("sl.cycle_trigger = ?")
        params.append(cycle_trigger)

    if hide_system:
        conditions.append(
            "NOT (COALESCE(sl.cycle_trigger, '') = 'system' "
            "OR (sl.instance_id IS NULL AND sl.action = 'info'))"
        )

    if before is not None:
        conditions.append("sl.timestamp < ?")
        params.append(before)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            sl.id,
            sl.instance_id,
            CASE
                WHEN sl.instance_id IS NULL THEN 'System'
                WHEN i.name IS NULL THEN 'Deleted'
                ELSE i.name
            END AS instance_name,
            sl.item_id,
            sl.item_type,
            sl.search_kind,
            sl.cycle_id,
            sl.cycle_trigger,
            sl.item_label,
            sl.action,
            sl.reason,
            sl.message,
            CASE
                WHEN sl.cycle_id IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM search_log sl2
                    WHERE sl2.cycle_id = sl.cycle_id
                      AND sl2.action = 'searched'
                ) THEN 'progress'
                ELSE 'no_progress'
            END AS cycle_progress,
            CASE
                WHEN sl.cycle_id IS NULL THEN NULL
                ELSE (
                    SELECT COUNT(*)
                    FROM search_log sl2
                    WHERE sl2.cycle_id = sl.cycle_id
                      AND sl2.action = 'searched'
                )
            END AS cycle_searched_count,
            CASE
                WHEN sl.cycle_id IS NULL THEN NULL
                ELSE (
                    SELECT COUNT(*)
                    FROM search_log sl3
                    WHERE sl3.cycle_id = sl.cycle_id
                      AND sl3.action = 'skipped'
                )
            END AS cycle_skipped_count,
            CASE
                WHEN sl.cycle_id IS NULL THEN NULL
                ELSE (
                    SELECT COUNT(*)
                    FROM search_log sl4
                    WHERE sl4.cycle_id = sl.cycle_id
                      AND sl4.action = 'error'
                )
            END AS cycle_error_count,
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

    raw = [dict(row) for row in rows]

    # Reorder so all rows belonging to the same cycle appear contiguously.
    # Rows without a cycle_id (system/info) are emitted in-place.
    # Cycle order is determined by each cycle's first appearance in the
    # timestamp-descending result, so the overall newest-first ordering is
    # preserved at the cycle level.
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in raw:
        cid = row["cycle_id"]
        if cid is not None:
            groups.setdefault(cid, []).append(row)

    reordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        cid = row["cycle_id"]
        if cid is None:
            reordered.append(row)
        elif cid not in seen:
            seen.add(cid)
            reordered.extend(groups[cid])

    return reordered


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/logs")
async def get_logs(
    instance_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    search_kind: str | None = Query(default=None),
    cycle_trigger: str | None = Query(default=None),
    hide_system: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=_LOG_LIMIT_DEFAULT, ge=1, le=_LOG_LIMIT_MAX),
) -> JSONResponse:
    """Return paginated log rows as JSON.

    Args:
        instance_id: Filter to a specific instance ID.
        action: Filter by action (``searched``, ``skipped``, ``error``, ``info``).
        search_kind: Filter by search pass kind (``missing`` or ``cutoff``).
        cycle_trigger: Filter by cycle trigger (``scheduled``, ``run_now``, ``system``).
        hide_system: When true, hide system lifecycle rows.
        before: Timestamp cursor; only return rows older than this ISO-8601 value.
        limit: Max rows (1–500, default 50).

    Returns:
        JSON array of log-row objects.
    """
    parsed_instance_id = _parse_instance_id(instance_id)
    parsed_action = action or None
    parsed_search_kind = _parse_search_kind(search_kind)
    parsed_cycle_trigger = _parse_cycle_trigger(cycle_trigger)
    parsed_hide_system = _parse_hide_system(hide_system)
    rows = await _query_logs(
        parsed_instance_id,
        parsed_action,
        parsed_search_kind,
        parsed_cycle_trigger,
        parsed_hide_system,
        before,
        limit,
    )
    return JSONResponse(rows)


@router.get("/api/logs/partial", response_class=HTMLResponse)
async def get_logs_partial(
    request: Request,
    instance_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    search_kind: str | None = Query(default=None),
    cycle_trigger: str | None = Query(default=None),
    hide_system: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=_LOG_LIMIT_DEFAULT, ge=1, le=_LOG_LIMIT_MAX),
) -> HTMLResponse:
    """Return a server-rendered <tbody> partial for HTMX swaps.

    Args:
        request: FastAPI request (required for template rendering).
        instance_id: Filter to a specific instance ID.
        action: Filter by action.
        search_kind: Filter by search pass kind.
        cycle_trigger: Filter by trigger type.
        hide_system: Whether to hide system lifecycle rows.
        before: Timestamp cursor.
        limit: Max rows.

    Returns:
        HTML fragment containing ``<tbody>`` rows.
    """
    parsed_instance_id = _parse_instance_id(instance_id)
    parsed_action = action or None
    parsed_search_kind = _parse_search_kind(search_kind)
    parsed_cycle_trigger = _parse_cycle_trigger(cycle_trigger)
    parsed_hide_system = _parse_hide_system(hide_system)
    load_more_limit = _compute_load_more_limit(limit)
    rows = await _query_logs(
        parsed_instance_id,
        parsed_action,
        parsed_search_kind,
        parsed_cycle_trigger,
        parsed_hide_system,
        before,
        limit,
    )
    return _get_templates().TemplateResponse(
        request=request,
        name="partials/log_rows.html",
        context={
            "rows": rows,
            # Pass back current filter values so the partial can render pagination
            "instance_id": parsed_instance_id,
            "action": parsed_action,
            "search_kind": parsed_search_kind,
            "cycle_trigger": parsed_cycle_trigger,
            "hide_system": parsed_hide_system,
            "before": before,
            "limit": limit,
            "load_more_limit": load_more_limit,
        },
    )

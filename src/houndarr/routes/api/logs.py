"""Logs API: paginated search_log entries with optional filters.

GET /api/logs         → JSON list of log rows (used by tests and external consumers)
GET /api/logs/partial → server-rendered <tbody> HTMX partial (used by the /logs page)
"""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from houndarr.routes._templates import get_templates
from houndarr.services.log_query import (
    LIMIT_DEFAULT,
    LIMIT_MAX,
    compute_load_more_limit,
    query_logs,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SEARCH_KINDS = {"missing", "cutoff", "upgrade"}
_CYCLE_TRIGGERS = {"scheduled", "run_now", "system"}


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
        raise HTTPException(
            status_code=422,
            detail="search_kind must be 'missing', 'cutoff', or 'upgrade'",
        )
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


def _partial_validation_error(detail: str) -> HTMLResponse:
    """Render a tbody-shaped 422 error for ``/api/logs/partial``.

    ``#log-tbody`` is the HTMX target; swapping FastAPI's default JSON
    error body into a ``<tbody>`` would render as raw ``{"detail":...}``
    text.  Shape the response as a single ``<tr>`` that matches the
    existing empty-state row (``colspan="10"``) so the swap preserves
    table structure.
    """
    safe = html.escape(detail)
    content = (
        '<tr id="log-error-row">'
        '<td colspan="10" class="px-3 py-14 text-center">'
        '<p class="text-sm font-medium text-red-300 font-sans">Invalid filter value.</p>'
        f'<p class="mt-1 text-xs text-red-400/80 font-sans">{safe}</p>'
        "</td></tr>"
    )
    return HTMLResponse(content=content, status_code=422)


@router.get("/api/logs")
async def get_logs(
    instance_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    search_kind: str | None = Query(default=None),
    cycle_trigger: str | None = Query(default=None),
    hide_system: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
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
    rows = await query_logs(
        instance_id=parsed_instance_id,
        action=parsed_action,
        search_kind=parsed_search_kind,
        cycle_trigger=parsed_cycle_trigger,
        hide_system=parsed_hide_system,
        before=before,
        limit=limit,
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
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
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
        HTML fragment containing ``<tbody>`` rows.  On a validation
        failure, returns a ``<tr>`` error row with status 422 instead
        of FastAPI's default JSON ``{"detail": ...}`` body so the HTMX
        swap preserves the table structure.
    """
    try:
        parsed_instance_id = _parse_instance_id(instance_id)
        parsed_search_kind = _parse_search_kind(search_kind)
        parsed_cycle_trigger = _parse_cycle_trigger(cycle_trigger)
        parsed_hide_system = _parse_hide_system(hide_system)
    except HTTPException as exc:
        return _partial_validation_error(str(exc.detail))

    parsed_action = action or None
    load_more_limit = compute_load_more_limit(limit)
    rows = await query_logs(
        instance_id=parsed_instance_id,
        action=parsed_action,
        search_kind=parsed_search_kind,
        cycle_trigger=parsed_cycle_trigger,
        hide_system=parsed_hide_system,
        before=before,
        limit=limit,
    )
    return get_templates().TemplateResponse(
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

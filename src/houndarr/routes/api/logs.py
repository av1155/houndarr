"""Logs API: paginated search_log entries with optional filters.

GET /api/logs         → JSON list of log rows (used by tests and external consumers)
GET /api/logs/partial → server-rendered <tbody> HTMX partial (used by the /logs page)

Route thinning (D.23).  The duplicated query-param parsing collapsed
into one :class:`_ParsedLogFilters` value object resolved via a
FastAPI ``Depends`` injection, and the shared service call moved to a
single helper.  Each handler is now three statements: take the
parsed filters, fetch the rows, wrap the response.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from houndarr.routes._templates import get_templates
from houndarr.services.log_query import (
    LIMIT_DEFAULT,
    LIMIT_MAX,
    compute_load_more_limit,
    head_snapshot,
    instance_accent_by_name,
    query_logs,
    search_log_has_any_user_row,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SEARCH_KINDS = {"missing", "cutoff", "upgrade"}
_CYCLE_TRIGGERS = {"scheduled", "run_now", "system"}


def parse_instance_ids(raw: list[str] | None) -> tuple[int, ...]:
    """Parse zero or more ``instance_id`` query params from an HTMX form.

    Accepts the repeated ``?instance_id=1&instance_id=2`` shape the
    multi-select filter submits, plus the empty-string sentinel the
    native "All instances" option used to emit.  De-duplicates while
    preserving request order so the SQL ``WHERE ... IN (...)`` clause
    stays deterministic and test assertions over ordering are stable.

    Args:
        raw: Query values FastAPI collects under ``list[str]``.  ``None``
            and empty lists both mean "no instance filter".

    Returns:
        Tuple of unique instance ids in request order; empty tuple when
        no meaningful value was supplied.

    Raises:
        HTTPException: If any non-empty value is not an integer.
    """
    if not raw:
        return ()
    seen: set[int] = set()
    ordered: list[int] = []
    for value in raw:
        if value is None or value == "":
            continue
        try:
            parsed = int(value)
        except ValueError as exc:  # pragma: no cover - defensive path
            raise HTTPException(status_code=422, detail="instance_id must be an integer") from exc
        if parsed not in seen:
            seen.add(parsed)
            ordered.append(parsed)
    return tuple(ordered)


def parse_search_kind(raw: str | None) -> str | None:
    """Parse optional search_kind query param."""
    if raw is None or raw == "":
        return None
    if raw not in _SEARCH_KINDS:
        raise HTTPException(
            status_code=422,
            detail="search_kind must be 'missing', 'cutoff', or 'upgrade'",
        )
    return raw


def parse_cycle_trigger(raw: str | None) -> str | None:
    """Parse optional cycle_trigger query param."""
    if raw is None or raw == "":
        return None
    if raw not in _CYCLE_TRIGGERS:
        raise HTTPException(
            status_code=422,
            detail="cycle_trigger must be 'scheduled', 'run_now', or 'system'",
        )
    return raw


def parse_hide_system(raw: str | None) -> bool:
    """Parse hide_system checkbox/select values from query params."""
    if raw is None or raw == "":
        return False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=422, detail="hide_system must be a boolean")


def parse_hide_skipped(raw: str | None) -> bool:
    """Parse hide_skipped checkbox/select values from query params.

    Mirrors :func:`parse_hide_system` so both filter toggles accept the
    same truthy / falsy vocabulary the HTMX form serialises from the
    Noise chip-switches.
    """
    if raw is None or raw == "":
        return False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=422, detail="hide_skipped must be a boolean")


@dataclass(frozen=True, slots=True)
class _ParsedLogFilters:
    """Validated filter bundle shared by both log route handlers.

    Populated by :func:`_resolve_filters` via a FastAPI ``Depends``
    injection; unpacked by :func:`_fetch_filtered_rows` into the
    service-level :func:`query_logs` call.  The dataclass kept private
    because its field set mirrors the service kwargs one-for-one and
    is not part of any public contract.
    """

    instance_ids: tuple[int, ...]
    action: str | None
    search_kind: str | None
    cycle_trigger: str | None
    hide_system: bool
    hide_skipped: bool
    before: str | None
    limit: int


def _resolve_filters(
    instance_id: list[str] = Query(default_factory=list),
    action: str | None = Query(default=None),
    search_kind: str | None = Query(default=None),
    cycle_trigger: str | None = Query(default=None),
    hide_system: str | None = Query(default=None),
    hide_skipped: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
) -> _ParsedLogFilters:
    """FastAPI dependency: bind + validate the log-filter query params.

    Raises :class:`HTTPException` 422 on any individual parser failure.
    The JSON endpoint lets FastAPI surface the default 422 body; the
    HTMX partial endpoint catches the exception and renders a tbody
    error row instead (see :func:`_partial_validation_error`).
    """
    return _ParsedLogFilters(
        instance_ids=parse_instance_ids(instance_id),
        action=action or None,
        search_kind=parse_search_kind(search_kind),
        cycle_trigger=parse_cycle_trigger(cycle_trigger),
        hide_system=parse_hide_system(hide_system),
        hide_skipped=parse_hide_skipped(hide_skipped),
        before=before,
        limit=limit,
    )


async def _fetch_filtered_rows(filters: _ParsedLogFilters) -> list[dict[str, Any]]:
    """Dispatch the parsed filter bundle to :func:`query_logs`."""
    return await query_logs(
        instance_ids=filters.instance_ids,
        action=filters.action,
        search_kind=filters.search_kind,
        cycle_trigger=filters.cycle_trigger,
        hide_system=filters.hide_system,
        hide_skipped=filters.hide_skipped,
        before=filters.before,
        limit=filters.limit,
    )


def _partial_validation_error(detail: str) -> HTMLResponse:
    """Render a feed-shaped 422 error for ``/api/logs/partial``.

    ``#log-feed`` is the HTMX target; swapping FastAPI's default JSON
    error body into a ``<section>`` would render as raw
    ``{"detail":...}`` text.  Shape the response as a ``<div
    class="empty">`` card that matches the zero-results branch of
    log_rows.html so the swap preserves the feed's visual language.
    """
    safe = html.escape(detail)
    content = (
        '<div id="log-error-row" class="empty empty--error" role="alert">'
        '<p class="empty__title">Invalid filter value.</p>'
        f"<p>{safe}</p>"
        "</div>"
    )
    return HTMLResponse(content=content, status_code=422)


@router.get("/api/logs")
async def get_logs(
    filters: Annotated[_ParsedLogFilters, Depends(_resolve_filters)],
) -> JSONResponse:
    """Return paginated log rows as JSON."""
    rows = await _fetch_filtered_rows(filters)
    return JSONResponse(rows)


@router.get("/api/logs/partial", response_class=HTMLResponse)
async def get_logs_partial(
    request: Request,
    instance_id: list[str] = Query(default_factory=list),
    action: str | None = Query(default=None),
    search_kind: str | None = Query(default=None),
    cycle_trigger: str | None = Query(default=None),
    hide_system: str | None = Query(default=None),
    hide_skipped: str | None = Query(default=None),
    before: str | None = Query(default=None),
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
) -> HTMLResponse:
    """Return a server-rendered partial for HTMX swaps.

    The partial endpoint does not use the ``Depends(_resolve_filters)``
    shortcut the JSON endpoint uses because a validation failure has
    to render as feed-shaped HTML instead of FastAPI's default JSON
    422 body; intercepting :class:`HTTPException` from inside the
    dependency is not a supported FastAPI pattern.  The query-param
    wiring therefore stays on the handler and the parser is called
    inline inside a ``try`` block.
    """
    try:
        filters = _resolve_filters(
            instance_id=instance_id,
            action=action,
            search_kind=search_kind,
            cycle_trigger=cycle_trigger,
            hide_system=hide_system,
            hide_skipped=hide_skipped,
            before=before,
            limit=limit,
        )
    except HTTPException as exc:
        return _partial_validation_error(str(exc.detail))

    rows = await _fetch_filtered_rows(filters)
    accent_map = await instance_accent_by_name()
    # Distinguish "table is empty" from "filters excluded everything"
    # so the partial picks the right empty-state copy.  Pagination
    # responses (``before`` is set, response targets ``#pagination-row``)
    # never reach the empty-state markup, so the probe is skipped there.
    log_db_empty = (not rows) and filters.before is None and not await search_log_has_any_user_row()
    return get_templates().TemplateResponse(
        request=request,
        name="partials/log_rows.html",
        context={
            "rows": rows,
            # Pass back current filter values so the partial can render pagination.
            "instance_ids": filters.instance_ids,
            "action": filters.action,
            "search_kind": filters.search_kind,
            "cycle_trigger": filters.cycle_trigger,
            "hide_system": filters.hide_system,
            "hide_skipped": filters.hide_skipped,
            "before": filters.before,
            "limit": filters.limit,
            "load_more_limit": compute_load_more_limit(filters.limit),
            # Cycle accent colours must survive the HTMX append on
            # "Load older"; omitting this context key falls the
            # template through to the default gray fallback.
            "instance_accent_by_name": accent_map,
            "log_db_empty": log_db_empty,
        },
    )


@router.get("/api/logs/head")
async def get_logs_head(
    since_cycle_id: str | None = Query(default=None),
) -> JSONResponse:
    """Return the newest cycle identifier plus a delta count since a cursor.

    Powers the Logs page live-tail banner.  The client polls this on the
    same 30 s cadence the dashboard uses against ``/api/status``, passing
    its top-of-feed ``cycle_id`` as ``since_cycle_id``; the response tells
    the page whether new cycles landed so it can render the "N new entries"
    banner without forcing a full partial swap while the user is scrolled.
    Filter-unaware in v1: the count does not honour the active filter
    chips, so a "new" banner can surface cycles the user has filtered out.
    Switching to filter-aware counts is a post-release iteration if the
    naive count misleads.
    """
    snapshot = await head_snapshot(since_cycle_id)
    return JSONResponse(snapshot)

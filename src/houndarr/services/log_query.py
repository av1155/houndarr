"""Log-query service: paginated ``search_log`` reads with cycle aggregates.

Track D.9 lifts the dynamic ``search_log`` SQL out of
:mod:`houndarr.routes.api.logs` so the route handler can stay short
and the view-layer aggregates (cycle progress, per-cycle searched /
skipped / error counts) live next to the query that produces them.
The route still owns parameter parsing and HTTP-shaped error
responses; this module owns SQL composition, cycle-grouping
post-processing, and the row-summary roll-up the page header
displays.

Limit constants live here because the SQL clamps to ``LIMIT_MAX`` and
the load-more chunk cap is a property of the query, not the route.
The route imports them back when it wants Pydantic ``Query`` defaults
that match the service's clamp.

Connection lifetime: unlike the metrics service in
:mod:`houndarr.services.metrics`, ``query_logs`` opens its own
connection.  The route only ever runs one log query per request and
does not need to share a handle with sibling reads, so the simpler
"open here, close here" pattern fits.
"""

from __future__ import annotations

from typing import Any

from houndarr.database import get_db

LIMIT_DEFAULT = 50
"""Default page size when the caller does not specify one."""

LIMIT_MAX = 5000
"""Hard cap on a single page's row count.

The route uses this as the ``Query(le=...)`` upper bound so an HTTP
client cannot ask for more rows than the SQL is willing to return,
and :func:`query_logs` clamps internally so direct service callers
still get the safety net.
"""

_LOAD_MORE_CHUNK_MAX = 100


def compute_load_more_limit(limit: int) -> int:
    """Return a bounded per-request chunk size for load-more pagination.

    The page's "Load more" button asks for the next chunk; this
    helper clamps it to a single hard cap so a malicious or careless
    request cannot pull thousands of rows in one swap.

    Args:
        limit: Caller-requested chunk size.

    Returns:
        ``min(max(1, limit), LIMIT_MAX, _LOAD_MORE_CHUNK_MAX)``.
    """
    bounded_limit = min(max(1, limit), LIMIT_MAX)
    return min(bounded_limit, _LOAD_MORE_CHUNK_MAX)


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Build the compact summary counts the page header surfaces.

    Counts the action distribution across the rows we are currently
    showing, plus per-cycle outcome buckets so the header can show
    "X cycles searched / Y skip-only" at a glance.  A cycle counts
    as ``progress`` when any of its rows had ``cycle_progress ==
    "progress"`` (the SQL stamps that on every row of a cycle that
    contains at least one ``searched`` action).

    Args:
        rows: Rows from :func:`query_logs`.  An empty list is valid
            and returns the all-zero summary.

    Returns:
        Dict with eight integer counters, all keyed by the field
        names the template binds to.
    """
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


async def query_logs(
    *,
    instance_id: int | None = None,
    action: str | None = None,
    search_kind: str | None = None,
    cycle_trigger: str | None = None,
    hide_system: bool = False,
    before: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> list[dict[str, Any]]:
    """Fetch a paginated, filtered slice of ``search_log`` rows.

    The SQL stamps four cycle-aggregate columns on every row
    (``cycle_progress``, ``cycle_searched_count``,
    ``cycle_skipped_count``, ``cycle_error_count``) via correlated
    subqueries so the page header can display per-cycle rollups
    without a second query.  After the fetch, rows that share a
    ``cycle_id`` get reordered to be contiguous; rows without one
    (system / info) stay in-place.  Cycle order is determined by
    each cycle's first appearance in the timestamp-descending
    result, so the overall newest-first ordering is preserved at
    the cycle level.

    Args:
        instance_id: Restrict to one instance, or ``None`` for all.
        action: Filter by ``action`` column value, or ``None``.
        search_kind: ``"missing"`` / ``"cutoff"`` / ``"upgrade"``,
            or ``None``.
        cycle_trigger: ``"scheduled"`` / ``"run_now"`` /
            ``"system"``, or ``None``.
        hide_system: When ``True`` strip system-lifecycle rows
            (``cycle_trigger='system'`` and the
            ``instance_id IS NULL AND action='info'`` boot-status
            rows).
        before: ISO-8601 timestamp cursor for forward pagination;
            only rows with ``timestamp < before`` are returned.
        limit: Page size.  Clamped to ``[1, LIMIT_MAX]`` so callers
            cannot bypass the cap by passing a huge number.

    Returns:
        List of dict rows with the column names the template binds
        to plus the four cycle-aggregate columns.
    """
    limit = min(max(1, limit), LIMIT_MAX)

    conditions: list[str] = []
    params: list[str | int] = []

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

    async with get_db() as db, db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    raw = [dict(row) for row in rows]

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

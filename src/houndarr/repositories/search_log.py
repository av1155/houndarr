"""Search-log aggregate: SQL boundary for the ``search_log`` table.

Implements the :class:`houndarr.protocols.SearchLogRepository`
contract plus the retention purge.  The engine's ``_write_log``
helper in :mod:`houndarr.engine.search_loop` delegates to
:func:`insert_log_row`; the golden-log characterisation test in
``tests/test_engine/test_golden_search_log.py`` pins the column
ordering and NULL-vs-value handling so the insert shape stays
byte-equal.  The ``/api/logs`` route is backed by
:mod:`houndarr.services.log_query`, which composes
:func:`fetch_log_rows` with the filter and pagination logic the
route needs.

Timestamps: the ``search_log.timestamp`` column is a ``DEFAULT
strftime(...)`` at the schema level, so inserts leave it untouched
and SQLite assigns the value atomically at commit time.  This
repository does not accept or return a timestamp on the insert path:
every other call site that needs a row's timestamp reads it back
via :func:`fetch_log_rows`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from houndarr.database import get_db

# Columns the fetch query is allowed to filter on.  Declared once so the
# dynamic WHERE builder in :func:`fetch_log_rows` stays table-driven and
# only accepts parameter names that map directly to real columns; the
# allowlist closes the SQL-injection surface that a free-form kwargs map
# would otherwise open.
_FETCH_FILTER_COLUMNS: frozenset[str] = frozenset(
    {"instance_id", "action", "search_kind", "cycle_id"}
)


async def insert_log_row(
    *,
    instance_id: int | None,
    item_id: int | None,
    item_type: str | None,
    action: str,
    search_kind: str | None = None,
    cycle_id: str | None = None,
    cycle_trigger: str | None = None,
    item_label: str | None = None,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    """Insert a single row into ``search_log``.

    The column list is fixed and matches the v13 schema exactly.
    ``timestamp`` is not a parameter: the column has a ``DEFAULT
    strftime(...)`` spec that SQLite resolves on commit, which keeps
    the write path side-effect-free w.r.t. the clock.

    Args:
        instance_id: Owning instance primary key, or ``None`` for
            system-scope rows (cycle-level info / error messages that
            are not tied to a specific instance).
        item_id: Item primary key as reported by the *arr, or ``None``
            for cycle-scope rows.
        item_type: One of the :class:`~houndarr.enums.ItemType` string
            values, or ``None``.  The column has a CHECK constraint
            that rejects unknown values, so callers that pass arbitrary
            strings here will see a constraint violation at commit.
        action: One of the :class:`~houndarr.enums.SearchAction` string
            values (``"searched"`` / ``"skipped"`` / ``"error"`` /
            ``"info"``).  Column has a CHECK constraint.
        search_kind: ``"missing"`` / ``"cutoff"`` / ``"upgrade"``, or
            ``None`` for cycle-scope rows.
        cycle_id: Shared identifier joining all rows from one cycle,
            or ``None`` for out-of-cycle writes.
        cycle_trigger: ``"scheduled"`` / ``"run_now"`` / ``"system"``,
            or ``None``.
        item_label: Human-readable label for the item, or ``None``.
        reason: Structured skip reason for ``action='skipped'`` rows.
        message: Free-form detail for ``action='error'`` / ``'info'``
            rows.
    """
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO search_log
                (
                    instance_id,
                    item_id,
                    item_type,
                    search_kind,
                    cycle_id,
                    cycle_trigger,
                    item_label,
                    action,
                    reason,
                    message
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instance_id,
                item_id,
                item_type,
                search_kind,
                cycle_id,
                cycle_trigger,
                item_label,
                action,
                reason,
                message,
            ),
        )
        await db.commit()


async def fetch_log_rows(
    *,
    instance_id: int | None = None,
    action: str | None = None,
    search_kind: str | None = None,
    cycle_id: str | None = None,
    limit: int = 100,
    after_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return a filtered page of ``search_log`` rows.

    Each filter defaults to ``None`` (no restriction).  ``after_id``
    acts as a descending cursor: pass the ``id`` of the last row from
    a previous page to fetch the next chunk.  Rows sort by
    ``timestamp DESC, id DESC`` so the newest row is always first,
    which matches the UI's reverse-chronological listing.

    The cycle-aggregate subqueries used by the ``/api/logs`` route
    live in the route module for now; this repository returns the
    raw column values.  D.9 will introduce a log-query service that
    layers the aggregates on top of this primitive.

    Args:
        instance_id: Filter to rows owned by this instance id.
        action: Filter to rows with this action string.
        search_kind: Filter to rows with this search-kind string.
        cycle_id: Filter to rows with this cycle identifier.
        limit: Maximum number of rows to return.  Caller-enforced
            positive integer; there is no server-side cap.
        after_id: Return rows with ``id < after_id`` (for forward
            pagination through a reverse-chronological page).

    Returns:
        List of dict rows; each dict has the column names as keys.
    """
    candidate_filters: dict[str, Any] = {
        "instance_id": instance_id,
        "action": action,
        "search_kind": search_kind,
        "cycle_id": cycle_id,
    }
    conditions: list[str] = []
    values: list[Any] = []
    for column, value in candidate_filters.items():
        if value is None:
            continue
        if column not in _FETCH_FILTER_COLUMNS:
            continue
        conditions.append(f"{column} = ?")
        values.append(value)

    if after_id is not None:
        conditions.append("id < ?")
        values.append(after_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    values.append(int(limit))

    sql = f"SELECT * FROM search_log {where_clause} ORDER BY timestamp DESC, id DESC LIMIT ?"  # noqa: S608  # nosec B608
    async with get_db() as db:
        async with db.execute(sql, values) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def fetch_recent_searches(
    instance_id: int,
    *,
    search_kind: str,
    within_seconds: int,
) -> int:
    """Count ``action='searched'`` rows in the trailing window.

    Used by the engine's hourly-cap gate: every pass counts how many
    search commands have landed for the same (instance, kind) pair in
    the trailing ``within_seconds`` and stops early once the cap is
    reached.  The query is parameterised so callers can reuse the
    boundary for short-window throttles too; the current engine path
    passes ``3600``.

    Args:
        instance_id: Owning instance primary key.
        search_kind: ``"missing"`` / ``"cutoff"`` / ``"upgrade"``.
        within_seconds: Trailing window length in seconds.  Non-positive
            values return ``0`` without touching the database.

    Returns:
        Number of ``searched`` rows for the (instance, search_kind)
        pair inside the window.
    """
    if within_seconds <= 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM search_log
            WHERE instance_id = ?
              AND action = 'searched'
              AND search_kind = ?
              AND timestamp > ?
            """,
            (instance_id, search_kind, cutoff_iso),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def fetch_latest_missing_reason(
    instance_id: int,
    item_id: int,
    item_type: str,
) -> str | None:
    """Return the ``reason`` from the most recent missing-pass log for *ref*.

    Used by the release-timing retry branch in the engine search loop
    to decide whether an item currently on cooldown should be retried
    (because the last logged skip reason was pre-release or
    post-release-grace, both of which can now have elapsed) or left
    alone.

    Args:
        instance_id: Owning instance primary key.
        item_id: *arr per-type item identifier.
        item_type: ``ItemType`` string value (``"episode"``,
            ``"season"``, ``"movie"``, ``"album"``, ``"book"``,
            ``"author"``, ``"series"``, ``"artist"``).

    Returns:
        The ``reason`` column value from the newest matching row, or
        ``None`` when no missing-pass row exists or the row's reason
        is NULL.
    """
    async with get_db() as db:
        async with db.execute(
            """
            SELECT reason
            FROM search_log
            WHERE instance_id = ?
              AND item_id = ?
              AND item_type = ?
              AND search_kind = 'missing'
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (instance_id, item_id, item_type),
        ) as cur:
            row = await cur.fetchone()
    return str(row[0]) if row and row[0] is not None else None


async def fetch_active_error_instance_ids() -> set[int]:
    """Return the set of instance IDs whose newest log row is an error.

    Used by the settings page to paint the per-row status dot red and
    by the dashboard banner to flag active failures.  The window is
    narrowed to the last 48 hours so the ``ROW_NUMBER()`` partition
    only scans recent rows: an error that has not re-surfaced in two
    days is stale for the "is this instance healthy right now?"
    question, and a genuinely stuck instance writes fresh error rows
    well inside that window.

    The cutoff uses ``strftime('%Y-%m-%dT%H:%M:%fZ', ...)`` so the
    boundary matches the stored column format exactly.  SQLite
    compares TEXT lexicographically, and ``datetime('now',
    '-2 days')`` returns a space-separated value; at position 10 of
    the comparison that space (0x20) sorts below the stored value's
    ``T`` (0x54), which would let same-calendar-day rows older than
    48 hours slip through.  Matching formats makes the comparison a
    pure ISO-8601 string compare and restores the advertised window.

    Returns:
        Set of instance IDs whose newest ``search_log`` row (within
        the 48h window) has ``action='error'``.  Empty set when no
        instance is currently failing.
    """
    sql = """
    SELECT instance_id FROM (
        SELECT instance_id, action,
               ROW_NUMBER() OVER (
                   PARTITION BY instance_id ORDER BY timestamp DESC, id DESC
               ) AS rn
        FROM search_log
        WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-2 days')
    )
    WHERE rn = 1 AND action = 'error'
    """
    async with get_db() as db:
        async with db.execute(sql) as cur:
            rows = await cur.fetchall()
    return {int(row["instance_id"]) for row in rows}


async def delete_logs_for_instance(instance_id: int) -> int:
    """Delete every ``search_log`` row for *instance_id*.

    The table's ``instance_id`` FK uses ``ON DELETE SET NULL`` so
    deleting the owning instance row leaves the log rows intact with
    ``instance_id = NULL``.  This function is the explicit per-instance
    purge: intended for a future admin "clear history" flow.  No
    current caller exercises it; the method is implemented to match
    the :class:`~houndarr.protocols.SearchLogRepository` Protocol
    surface.

    Args:
        instance_id: Owning instance primary key.

    Returns:
        Number of rows deleted.
    """
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM search_log WHERE instance_id = ?",
            (instance_id,),
        )
        await db.commit()
        return cur.rowcount or 0


async def delete_all_logs() -> int:
    """Truncate the ``search_log`` table and return the removed row count.

    Backs the Admin > Maintenance > Clear all logs action.  The audit
    breadcrumb row ("Audit log cleared by admin ...") is written by
    :func:`insert_admin_audit` after this returns, so the DELETE and
    the follow-up INSERT are two separate statements against two
    separate connections; that keeps the truncate a pure wipe and
    lets concurrent supervisor writes interleave without corrupting
    the breadcrumb row.

    Returns:
        Number of rows that were removed (excluding the breadcrumb,
        which is written by the caller afterwards).
    """
    async with get_db() as db:
        cur = await db.execute("DELETE FROM search_log")
        await db.commit()
        return cur.rowcount or 0


async def insert_admin_audit(message: str) -> None:
    """Insert a single system-audit row into ``search_log``.

    Used by admin operations (policy reset, log clear, factory reset)
    to leave a breadcrumb on the Activity logs page
    (e.g. "Policy settings reset to defaults by admin").  The row is
    attributed to ``cycle_trigger='system'`` and ``action='info'`` so
    it sorts alongside the scheduler's lifecycle events rather than a
    real search result.  ``instance_id`` is NULL because these are
    library-wide operations, not per-instance.

    Args:
        message: Free-text audit message written to the ``message``
            column verbatim.
    """
    async with get_db() as db:
        await db.execute(
            "INSERT INTO search_log (instance_id, cycle_trigger, action, message)"
            " VALUES (NULL, 'system', 'info', ?)",
            (message,),
        )
        await db.commit()


async def purge_old_logs(retention_days: int) -> int:
    """Delete ``search_log`` rows older than *retention_days* days.

    Called by the app lifespan at startup and by
    ``_periodic_log_retention`` on a 24-hour cadence to prevent
    unbounded log growth on long-running instances.  Living in the
    repository layer (alongside every other ``search_log`` SQL writer)
    keeps the table's SQL entirely owned here.

    Args:
        retention_days: Rows with a ``timestamp`` older than this many
            days are deleted.  Pass ``0`` or a negative value to
            disable purging; the function then returns ``0`` without
            issuing any SQL.

    Returns:
        Number of rows deleted (``0`` when retention is disabled or
        nothing matched the cut-off).
    """
    if retention_days <= 0:
        return 0

    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM search_log WHERE timestamp < datetime('now', ? || ' days')",
            (f"-{retention_days}",),
        )
        await db.commit()
        return cur.rowcount or 0

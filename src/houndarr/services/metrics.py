"""Dashboard-metrics service: SQL aggregations for ``/api/status``.

Track D.8 lifts the metrics SQL out of
:mod:`houndarr.routes.api.status` so the route handler can stay
short and the queries are testable without spinning up FastAPI.
The functions live here, paired with the SQL constants that drive
them; the route composes them and assembles the JSON envelope.

Connection lifetime stays with the caller: every public function
takes an open :class:`aiosqlite.Connection` (typically the route's
``async with get_db() as db:`` scope) so all five lookups run on the
same SQLite handle.  That keeps the read-side consistent and lets
the route batch its queries inside one ``BEGIN`` / connection close
without crossing a service boundary mid-transaction.

The Python-side aggregation in :func:`gather_cooldown_data` (per-row
unlock-time computation, batch-clone spread for ``unlocking_next``)
moves with the SQL: it has no useful lifetime apart from the rows
the SQL produces, and pulling it apart would force the caller to
re-derive the same per-row context.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

_METRICS_SQL = """
SELECT
    instance_id,
    SUM(CASE WHEN action = 'searched'
             AND julianday(timestamp) >= julianday('now', '-24 hours')
             THEN 1 ELSE 0 END)
        AS searched_24h,
    SUM(CASE WHEN action = 'skipped'
             AND julianday(timestamp) >= julianday('now', '-24 hours')
             THEN 1 ELSE 0 END)
        AS skipped_24h,
    SUM(CASE WHEN action = 'error'
             AND julianday(timestamp) >= julianday('now', '-24 hours')
             THEN 1 ELSE 0 END)
        AS errors_24h,
    MAX(CASE WHEN action = 'searched' THEN timestamp END)
        AS last_search_at
FROM search_log
WHERE instance_id IN ({placeholders})
GROUP BY instance_id
"""

_LAST_ACTIVITY_SQL = """
SELECT instance_id, action, timestamp
FROM (
    SELECT instance_id, action, timestamp,
           ROW_NUMBER() OVER (
               PARTITION BY instance_id ORDER BY timestamp DESC
           ) AS rn
    FROM search_log
    WHERE instance_id IN ({placeholders})
      AND action IN ('searched', 'skipped', 'error')
)
WHERE rn = 1
"""

# Latest row per instance regardless of action.  Used for the error banner's
# "latest-row" self-clearing trigger: when the newest row is action='error'
# we render the banner; when the newest is any non-error row the banner
# clears on the next poll.
_LATEST_ROW_SQL = """
SELECT instance_id, action, timestamp, reason, message
FROM (
    SELECT instance_id, action, timestamp, reason, message,
           ROW_NUMBER() OVER (
               PARTITION BY instance_id ORDER BY timestamp DESC
           ) AS rn
    FROM search_log
    WHERE instance_id IN ({placeholders})
)
WHERE rn = 1
"""

# Error run-length since the last non-error row.  Count scoped per instance.
_ERROR_STREAK_SQL = """
SELECT COUNT(*) AS count
FROM search_log
WHERE instance_id = ?
  AND action = 'error'
  AND timestamp > COALESCE(
      (SELECT MAX(timestamp) FROM search_log
       WHERE instance_id = ? AND action != 'error'),
      '1970-01-01T00:00:00Z'
  )
"""

# Lifetime search count (all time, action='searched') and last dispatch
# timestamp per instance.
_LIFETIME_SQL = """
SELECT
    instance_id,
    SUM(CASE WHEN action = 'searched' THEN 1 ELSE 0 END) AS lifetime_searched,
    MAX(CASE WHEN action = 'searched' THEN timestamp END) AS last_dispatch_at
FROM search_log
WHERE instance_id IN ({placeholders})
GROUP BY instance_id
"""

# Global recent-dispatches strip: last N rows across all instances within the
# past 7 days.  Joined against instances for name+type so the client can
# color each row in the owning instance's type color.
_RECENT_SEARCHES_SQL = """
SELECT
    sl.instance_id,
    i.name AS instance_name,
    i.type AS instance_type,
    sl.item_label,
    sl.timestamp
FROM search_log sl
JOIN instances i ON i.id = sl.instance_id
WHERE sl.action = 'searched'
  AND julianday(sl.timestamp) >= julianday('now', '-7 days')
ORDER BY sl.timestamp DESC
LIMIT ?
"""

# Per-instance cooldown rows with the most recent searched kind attached.
# Small correlated subqueries are fine here: cooldowns is typically <100 rows
# per instance and each subquery uses the idx_search_log_instance index.
_COOLDOWNS_SQL = """
SELECT
    c.instance_id,
    c.item_id,
    c.item_type,
    c.searched_at,
    (SELECT sl.item_label FROM search_log sl
     WHERE sl.instance_id = c.instance_id
       AND sl.item_id = c.item_id
       AND sl.item_type = c.item_type
       AND sl.action = 'searched'
     ORDER BY sl.timestamp DESC LIMIT 1) AS item_label,
    (SELECT sl.search_kind FROM search_log sl
     WHERE sl.instance_id = c.instance_id
       AND sl.item_id = c.item_id
       AND sl.item_type = c.item_type
       AND sl.action = 'searched'
     ORDER BY sl.timestamp DESC LIMIT 1) AS last_search_kind
FROM cooldowns c
WHERE c.instance_id IN ({placeholders})
"""

EMPTY_METRICS: dict[str, Any] = {
    "searched_24h": 0,
    "skipped_24h": 0,
    "errors_24h": 0,
    "last_search_at": None,
}


# Columns pulled from the instances table for the dashboard status
# payload.  ``encrypted_api_key`` is deliberately excluded so the
# Fernet decrypt round trip never runs on this hot path; the status
# endpoint never needs plaintext credentials.
_STATUS_INSTANCE_COLS = (
    "id, name, type, enabled, batch_size, sleep_interval_mins, hourly_cap,"
    " cooldown_days, cutoff_enabled, cutoff_batch_size,"
    " cutoff_cooldown_days,"
    " post_release_grace_hrs, queue_limit,"
    " upgrade_enabled, upgrade_cooldown_days,"
    " monitored_total, unreleased_count, snapshot_refreshed_at"
)

# Default cooldown shape when an instance has no open cooldown rows.
# Kept in one place so the route assembler and the per-row gatherer
# stay in lockstep.
_EMPTY_COOLDOWN: dict[str, Any] = {
    "cooldown_breakdown": {"missing": 0, "cutoff": 0, "upgrade": 0},
    "unlocking_next": [],
    "cooldown_total": 0,
}


def _build_instance_status_row(
    inst: aiosqlite.Row,
    *,
    window_metrics: dict[str, Any],
    last_activity: tuple[str | None, str | None],
    lifetime: dict[str, Any],
    active_error: dict[str, Any] | None,
    cooldown: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one instance entry for the ``/api/status`` envelope.

    The route's per-row dict assembly moved here unchanged: same keys,
    same coercions, same ordering.  The only reason this is a
    separate function instead of inlined into
    :func:`gather_dashboard_status` is readability; a 30-line literal
    inside a for-loop inside an async function reads worse than a
    named helper with argument-level documentation via the keyword
    call site.
    """
    action, at = last_activity
    refreshed = inst["snapshot_refreshed_at"]
    return {
        "id": inst["id"],
        "name": inst["name"],
        "type": inst["type"],
        "enabled": bool(inst["enabled"]),
        "last_search_at": window_metrics["last_search_at"],
        "searched_24h": window_metrics["searched_24h"],
        "skipped_24h": window_metrics["skipped_24h"],
        "errors_24h": window_metrics["errors_24h"],
        "last_activity_action": action,
        "last_activity_at": at,
        "batch_size": inst["batch_size"],
        "sleep_interval_mins": inst["sleep_interval_mins"],
        "hourly_cap": inst["hourly_cap"],
        "cooldown_days": inst["cooldown_days"],
        "cutoff_enabled": bool(inst["cutoff_enabled"]),
        "cutoff_batch_size": inst["cutoff_batch_size"],
        "post_release_grace_hrs": inst["post_release_grace_hrs"],
        "queue_limit": inst["queue_limit"],
        "lifetime_searched": lifetime["lifetime_searched"],
        "last_dispatch_at": lifetime["last_dispatch_at"],
        "active_error": active_error,
        "cooldown_breakdown": cooldown["cooldown_breakdown"],
        "unlocking_next": cooldown["unlocking_next"],
        "cooldown_total": cooldown["cooldown_total"],
        "monitored_total": int(inst["monitored_total"]),
        "unreleased_count": int(inst["unreleased_count"]),
        "snapshot_refreshed_at": str(refreshed) if refreshed else None,
        "upgrade_enabled": bool(inst["upgrade_enabled"]),
        "upgrade_cooldown_days": int(inst["upgrade_cooldown_days"]),
    }


async def gather_dashboard_status(db: aiosqlite.Connection) -> dict[str, list[dict[str, Any]]]:
    """Build the full ``/api/status`` JSON envelope against an open connection.

    One SQL fetch for the instance rows, then the five per-cycle
    gathers (window metrics, lifetime metrics, active errors, cooldown
    data, recent searches), then the per-instance assembly.  The five
    gathers share the same connection so they run against a stable
    snapshot of the instance table.

    Args:
        db: Open :class:`aiosqlite.Connection`.  The caller owns the
            connection lifetime; this function issues reads only.

    Returns:
        ``{"instances": [...], "recent_searches": [...]}`` ready for
        ``JSONResponse``.  Empty installs short-circuit with both
        lists empty.
    """
    async with db.execute(
        f"SELECT {_STATUS_INSTANCE_COLS} FROM instances ORDER BY id ASC"  # noqa: S608  # nosec B608
    ) as cur:
        instances = await cur.fetchall()

    if not instances:
        return {"instances": [], "recent_searches": []}

    instance_ids = [row["id"] for row in instances]
    metrics_map, activity_map = await gather_window_metrics(db, instance_ids)
    lifetime_map = await gather_lifetime_metrics(db, instance_ids)
    error_map = await gather_active_errors(db, instance_ids)
    cooldown_map = await gather_cooldown_data(db, list(instances))
    recent = await gather_recent_searches(db, limit=5)

    rows: list[dict[str, Any]] = []
    for inst in instances:
        iid = inst["id"]
        rows.append(
            _build_instance_status_row(
                inst,
                window_metrics=metrics_map.get(iid, EMPTY_METRICS),
                last_activity=activity_map.get(iid, (None, None)),
                lifetime=lifetime_map.get(iid, {"lifetime_searched": 0, "last_dispatch_at": None}),
                active_error=error_map.get(iid),
                cooldown=cooldown_map.get(iid, _EMPTY_COOLDOWN),
            )
        )
    return {"instances": rows, "recent_searches": recent}


async def gather_window_metrics(
    db: aiosqlite.Connection,
    instance_ids: list[int],
) -> tuple[dict[int, dict[str, Any]], dict[int, tuple[str | None, str | None]]]:
    """Aggregate 24h search counts and last-activity per instance.

    Two queries: one ``SUM(CASE)`` rollup of searched / skipped /
    error counts in the last 24 hours plus the most recent
    ``searched`` timestamp; one ``ROW_NUMBER()`` window for the most
    recent searched/skipped/error row per instance.

    Args:
        db: Open aiosqlite connection.
        instance_ids: Instances to aggregate for.  Empty list returns
            two empty dicts.

    Returns:
        ``(metrics_by_id, last_activity_by_id)``: the second dict
        maps each id to ``(action, timestamp)``; ids with no
        qualifying rows are omitted.
    """
    if not instance_ids:
        return {}, {}

    placeholders = ",".join("?" * len(instance_ids))

    metrics: dict[int, dict[str, Any]] = {}
    async with db.execute(_METRICS_SQL.format(placeholders=placeholders), instance_ids) as cur:
        async for row in cur:
            iid = row["instance_id"]
            metrics[iid] = {
                "searched_24h": int(row["searched_24h"] or 0),
                "skipped_24h": int(row["skipped_24h"] or 0),
                "errors_24h": int(row["errors_24h"] or 0),
                "last_search_at": str(row["last_search_at"]) if row["last_search_at"] else None,
            }

    activity: dict[int, tuple[str | None, str | None]] = {}
    async with db.execute(
        _LAST_ACTIVITY_SQL.format(placeholders=placeholders), instance_ids
    ) as cur:
        async for row in cur:
            activity[row["instance_id"]] = (str(row["action"]), str(row["timestamp"]))

    return metrics, activity


async def gather_lifetime_metrics(
    db: aiosqlite.Connection,
    instance_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Return per-instance ``lifetime_searched`` count + ``last_dispatch_at``.

    Args:
        db: Open aiosqlite connection.
        instance_ids: Instances to aggregate for.

    Returns:
        Map of ``instance_id`` to
        ``{"lifetime_searched": int, "last_dispatch_at": str | None}``.
        Instances with no qualifying rows are omitted from the result.
    """
    if not instance_ids:
        return {}
    placeholders = ",".join("?" * len(instance_ids))
    out: dict[int, dict[str, Any]] = {}
    async with db.execute(_LIFETIME_SQL.format(placeholders=placeholders), instance_ids) as cur:
        async for row in cur:
            out[row["instance_id"]] = {
                "lifetime_searched": int(row["lifetime_searched"] or 0),
                "last_dispatch_at": (
                    str(row["last_dispatch_at"]) if row["last_dispatch_at"] else None
                ),
            }
    return out


async def gather_active_errors(
    db: aiosqlite.Connection,
    instance_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Return banner data for instances whose newest row is an error.

    Self-clearing: when the supervisor's next cycle writes a non-error
    row the instance drops out of the result on the next poll.  An
    extra per-flagged-instance query computes the failure streak count
    since the last non-error row, so the banner can show "Nth
    consecutive failure" without scanning the whole log.

    Args:
        db: Open aiosqlite connection.
        instance_ids: Instances to consider.

    Returns:
        Map of ``instance_id`` to
        ``{"timestamp", "message", "reason", "failures_count"}`` for
        every instance currently in an error streak.
    """
    if not instance_ids:
        return {}
    placeholders = ",".join("?" * len(instance_ids))
    out: dict[int, dict[str, Any]] = {}
    async with db.execute(_LATEST_ROW_SQL.format(placeholders=placeholders), instance_ids) as cur:
        async for row in cur:
            if row["action"] != "error":
                continue
            iid = int(row["instance_id"])
            out[iid] = {
                "timestamp": str(row["timestamp"]) if row["timestamp"] else None,
                "message": str(row["message"]) if row["message"] else None,
                "reason": str(row["reason"]) if row["reason"] else None,
                "failures_count": 0,
            }
    for iid in out:
        async with db.execute(_ERROR_STREAK_SQL, (iid, iid)) as cur:
            streak_row = await cur.fetchone()
        out[iid]["failures_count"] = (
            int(streak_row["count"]) if streak_row and streak_row["count"] else 0
        )
    return out


async def gather_recent_searches(db: aiosqlite.Connection, limit: int = 5) -> list[dict[str, Any]]:
    """Return the most recent successful dispatches across all instances.

    The query is scoped to the past 7 days so an idle install does
    not surface ancient activity in the dashboard strip.

    Args:
        db: Open aiosqlite connection.
        limit: Maximum number of rows to return.

    Returns:
        List of dicts with ``instance_id`` / ``instance_name`` /
        ``instance_type`` / ``item_label`` / ``timestamp`` keys, newest
        first.
    """
    out: list[dict[str, Any]] = []
    async with db.execute(_RECENT_SEARCHES_SQL, (limit,)) as cur:
        async for row in cur:
            out.append(
                {
                    "instance_id": int(row["instance_id"]),
                    "instance_name": str(row["instance_name"]),
                    "instance_type": str(row["instance_type"]),
                    "item_label": str(row["item_label"]) if row["item_label"] else None,
                    "timestamp": str(row["timestamp"]),
                }
            )
    return out


async def gather_cooldown_data(
    db: aiosqlite.Connection,
    instances: list[aiosqlite.Row],
) -> dict[int, dict[str, Any]]:
    """Return per-instance ``cooldown_breakdown`` and ``unlocking_next``.

    ``cooldown_breakdown`` groups active cooldown rows by the most
    recent ``search_kind`` that landed for that item.  Rows with no
    matching search log entry fall back to ``"missing"``.

    ``unlocking_next`` surfaces three cooldown rows that represent
    the schedule: the soonest to unlock, the median, and the latest.
    Picking a spread (instead of the top 3 soonest) avoids rendering
    three rows with identical "11d 8h" labels when a batch of items
    was dispatched seconds apart and all unlock together.  Unlock
    time uses the cooldown window that matches the pass that actually
    wrote the row (``missing`` -> ``cooldown_days``, ``cutoff`` ->
    ``cutoff_cooldown_days``, ``upgrade`` -> ``upgrade_cooldown_days``),
    so upgrade-kind rows accurately show the long 90-day default
    instead of collapsing to the 14-day missing-pass minimum.

    Args:
        db: Open aiosqlite connection.
        instances: Pre-fetched ``aiosqlite.Row`` list from the
            ``instances`` table; the function reads
            ``id`` / ``cooldown_days`` / ``cutoff_cooldown_days`` /
            ``upgrade_cooldown_days`` from each row.

    Returns:
        Map of ``instance_id`` to
        ``{"cooldown_breakdown", "unlocking_next", "cooldown_total"}``.
    """
    if not instances:
        return {}
    instance_ids = [row["id"] for row in instances]
    placeholders = ",".join("?" * len(instance_ids))

    # Per-instance cooldown windows, one row per pass kind.  Used below
    # to convert each cooldown row's searched_at into an unlock_at via
    # the matching pass-specific cooldown_days value.
    config: dict[int, dict[str, int]] = {
        int(row["id"]): {
            "cooldown_days": int(row["cooldown_days"]),
            "cutoff_cooldown_days": int(row["cutoff_cooldown_days"]),
            "upgrade_cooldown_days": int(row["upgrade_cooldown_days"]),
        }
        for row in instances
    }

    out: dict[int, dict[str, Any]] = {
        iid: {
            "cooldown_breakdown": {"missing": 0, "cutoff": 0, "upgrade": 0},
            "unlocking_next": [],
            "cooldown_total": 0,
        }
        for iid in instance_ids
    }

    per_instance_rows: dict[int, list[dict[str, Any]]] = {iid: [] for iid in instance_ids}
    async with db.execute(_COOLDOWNS_SQL.format(placeholders=placeholders), instance_ids) as cur:
        async for row in cur:
            iid = int(row["instance_id"])
            kind = str(row["last_search_kind"]) if row["last_search_kind"] else "missing"
            bucket = kind if kind in ("missing", "cutoff", "upgrade") else "missing"
            out[iid]["cooldown_breakdown"][bucket] += 1
            out[iid]["cooldown_total"] += 1
            per_instance_rows[iid].append(
                {
                    "item_id": int(row["item_id"]),
                    "item_type": str(row["item_type"]),
                    "searched_at": str(row["searched_at"]),
                    "item_label": str(row["item_label"]) if row["item_label"] else None,
                    "last_search_kind": bucket,
                }
            )

    for iid, rows in per_instance_rows.items():
        cfg = config[iid]
        enriched: list[dict[str, Any]] = []
        for entry in rows:
            try:
                parsed = datetime.fromisoformat(entry["searched_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            kind = entry["last_search_kind"]
            if kind == "cutoff":
                days = cfg["cutoff_cooldown_days"]
            elif kind == "upgrade":
                days = cfg["upgrade_cooldown_days"]
            else:
                days = cfg["cooldown_days"]
            unlock = parsed + timedelta(days=days)
            enriched.append({**entry, "unlock_at": unlock})
        enriched.sort(key=lambda r: r["unlock_at"])
        # Drop past-unlock rows so the panel is actually future-looking;
        # items whose unlock has already passed will be cleared the next
        # time the engine runs.
        now = datetime.now(UTC)
        upcoming = [r for r in enriched if r["unlock_at"] > now]
        # Pick a spread across the schedule (soonest, median, latest) so
        # the three rows never collapse to a single batch's clone-unlock
        # time.  Batched dispatches finish within seconds of each other,
        # which makes a naive [:3] slice render three identical "11d 8h"
        # rows; the spread gives the user a real sense of the window.
        n = len(upcoming)
        if n == 0:
            picks: list[dict[str, Any]] = []
        elif n <= 3:
            picks = upcoming
        else:
            picks = [upcoming[0], upcoming[n // 2], upcoming[-1]]
        out[iid]["unlocking_next"] = [
            {
                "item_id": r["item_id"],
                "item_type": r["item_type"],
                "item_label": r["item_label"],
                "unlock_at": r["unlock_at"].isoformat(timespec="seconds"),
                "last_search_kind": r["last_search_kind"],
            }
            for r in picks
        ]
    return out

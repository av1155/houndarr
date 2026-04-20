"""Status API: per-instance search metrics and run-now trigger.

GET  /api/status             -> JSON envelope
                                ``{"instances": [...], "recent_searches": [...]}``
POST /api/instances/{id}/run-now -> trigger an immediate search cycle (202)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from houndarr.database import get_db
from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Columns needed from the instances table for the status response.
# Notably excludes encrypted_api_key to avoid Fernet decryption overhead.
_INSTANCE_COLS = (
    "id, name, type, enabled, batch_size, sleep_interval_mins, hourly_cap,"
    " cooldown_days, cutoff_enabled, cutoff_batch_size,"
    " cutoff_cooldown_days,"
    " post_release_grace_hrs, queue_limit,"
    " upgrade_enabled, upgrade_cooldown_days,"
    " monitored_total, unreleased_count, snapshot_refreshed_at"
)

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


async def _all_instance_metrics(
    db: aiosqlite.Connection,
    instance_ids: list[int],
) -> tuple[dict[int, dict[str, Any]], dict[int, tuple[str | None, str | None]]]:
    """Fetch aggregated search metrics and last-activity for all instances.

    Returns:
        A tuple of (metrics_by_id, last_activity_by_id).
    """
    if not instance_ids:
        return {}, {}

    placeholders = ",".join("?" * len(instance_ids))

    # Aggregated counters per instance
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

    # Most recent activity row per instance
    activity: dict[int, tuple[str | None, str | None]] = {}
    async with db.execute(
        _LAST_ACTIVITY_SQL.format(placeholders=placeholders), instance_ids
    ) as cur:
        async for row in cur:
            activity[row["instance_id"]] = (str(row["action"]), str(row["timestamp"]))

    return metrics, activity


_EMPTY_METRICS: dict[str, Any] = {
    "searched_24h": 0,
    "skipped_24h": 0,
    "errors_24h": 0,
    "last_search_at": None,
}


async def _lifetime_metrics(
    db: Any,  # noqa: ANN401
    instance_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Return per-instance lifetime_searched + last_dispatch_at."""
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


async def _active_errors(
    db: Any,  # noqa: ANN401
    instance_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Return ``{instance_id: {timestamp, message, failures_count}}`` for
    instances whose newest ``search_log`` row is ``action='error'``.

    Self-clearing: when the supervisor's next cycle writes a non-error row
    the instance drops out of the result on the next poll.
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
    # Enrichment pass: failure count since the last non-error row.  One
    # query per flagged instance keeps the common case (no errors) free.
    for iid in out:
        async with db.execute(_ERROR_STREAK_SQL, (iid, iid)) as cur:
            row = await cur.fetchone()
        out[iid]["failures_count"] = int(row["count"]) if row and row["count"] else 0
    return out


async def _recent_searches(db: Any, limit: int = 5) -> list[dict[str, Any]]:  # noqa: ANN401
    """Return last *limit* dispatches across all instances within 7 days."""
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


async def _cooldown_data(
    db: Any,  # noqa: ANN401
    instances: list[Any],
) -> dict[int, dict[str, Any]]:
    """Return per-instance ``cooldown_breakdown`` and ``unlocking_next``.

    ``cooldown_breakdown`` groups active cooldown rows by the most recent
    ``search_kind`` that landed for that item.  Rows with no matching
    search log entry fall back to ``"missing"``.

    ``unlocking_next`` surfaces three cooldown rows that represent the
    schedule: the soonest to unlock, the median, and the latest. Picking
    a spread (instead of the top 3 soonest) avoids rendering three rows
    with identical "11d 8h" labels when a batch of items was dispatched
    seconds apart and all unlock together. Unlock time uses the
    instance's enabled cooldown_days values (missing, cutoff when
    ``cutoff_enabled``, upgrade when ``upgrade_enabled``); the earliest
    applicable unlock time wins.
    """
    if not instances:
        return {}
    instance_ids = [row["id"] for row in instances]
    placeholders = ",".join("?" * len(instance_ids))

    # Pull settings per instance for unlock-time computation.
    config: dict[int, dict[str, Any]] = {}
    for row in instances:
        config[int(row["id"])] = {
            "cooldown_days": int(row["cooldown_days"]),
            "cutoff_cooldown_days": int(row["cutoff_cooldown_days"]),
            "cutoff_enabled": bool(row["cutoff_enabled"]),
            "upgrade_cooldown_days": int(row["upgrade_cooldown_days"]),
            "upgrade_enabled": bool(row["upgrade_enabled"]),
        }

    out: dict[int, dict[str, Any]] = {
        iid: {
            "cooldown_breakdown": {"missing": 0, "cutoff": 0, "upgrade": 0},
            "unlocking_next": [],
            "cooldown_total": 0,
        }
        for iid in instance_ids
    }

    # Collect all cooldown rows; compute unlock time per row in Python.
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
        # Lowest cooldown_days across enabled windows for this instance.
        candidate_windows = [cfg["cooldown_days"]]
        if cfg["cutoff_enabled"]:
            candidate_windows.append(cfg["cutoff_cooldown_days"])
        if cfg["upgrade_enabled"]:
            candidate_windows.append(cfg["upgrade_cooldown_days"])
        min_days = min(candidate_windows) if candidate_windows else cfg["cooldown_days"]
        enriched: list[dict[str, Any]] = []
        for row in rows:
            try:
                parsed = datetime.fromisoformat(row["searched_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            unlock = parsed + timedelta(days=min_days)
            enriched.append({**row, "unlock_at": unlock})
        enriched.sort(key=lambda r: r["unlock_at"])
        # Drop past-unlock rows so the panel is actually future-looking;
        # items whose unlock has already passed will be cleared the next
        # time the engine runs.
        now = datetime.now(UTC)
        upcoming = [r for r in enriched if r["unlock_at"] > now]
        # Pick a spread across the schedule (soonest, median, latest) so
        # the three rows never collapse to a single batch's clone-unlock
        # time. Batched dispatches finish within seconds of each other,
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/status")
async def get_status(request: Request) -> JSONResponse:  # noqa: ARG001
    """Return the dashboard status envelope.

    ``{"instances": [...], "recent_searches": [...]}``: each instance
    carries per-card fields (``monitored_total``, ``unreleased_count``,
    ``lifetime_searched``, ``last_dispatch_at``, ``active_error``,
    ``cooldown_breakdown``, ``unlocking_next``, plus the policy fields
    used by the chip row).  ``recent_searches`` is the global last-5
    dispatches over the past 7 days, joined against instances for the
    type-color rendering.
    """
    async with get_db() as db:
        async with db.execute(
            f"SELECT {_INSTANCE_COLS} FROM instances ORDER BY id ASC"  # noqa: S608  # nosec B608
        ) as cur:
            instances = await cur.fetchall()

        if not instances:
            return JSONResponse({"instances": [], "recent_searches": []})

        instance_ids = [row["id"] for row in instances]
        metrics_map, activity_map = await _all_instance_metrics(db, instance_ids)
        lifetime_map = await _lifetime_metrics(db, instance_ids)
        error_map = await _active_errors(db, instance_ids)
        cooldown_map = await _cooldown_data(db, list(instances))
        recent = await _recent_searches(db, limit=5)

    results: list[dict[str, Any]] = []
    for inst in instances:
        iid = inst["id"]
        m = metrics_map.get(iid, _EMPTY_METRICS)
        act_action, act_at = activity_map.get(iid, (None, None))
        lifetime = lifetime_map.get(iid, {"lifetime_searched": 0, "last_dispatch_at": None})
        cooldown = cooldown_map.get(
            iid,
            {
                "cooldown_breakdown": {"missing": 0, "cutoff": 0, "upgrade": 0},
                "unlocking_next": [],
                "cooldown_total": 0,
            },
        )
        results.append(
            {
                "id": iid,
                "name": inst["name"],
                "type": inst["type"],
                "enabled": bool(inst["enabled"]),
                "last_search_at": m["last_search_at"],
                "searched_24h": m["searched_24h"],
                "skipped_24h": m["skipped_24h"],
                "errors_24h": m["errors_24h"],
                "last_activity_action": act_action,
                "last_activity_at": act_at,
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
                "active_error": error_map.get(iid),
                "cooldown_breakdown": cooldown["cooldown_breakdown"],
                "unlocking_next": cooldown["unlocking_next"],
                "cooldown_total": cooldown["cooldown_total"],
                "monitored_total": int(inst["monitored_total"]),
                "unreleased_count": int(inst["unreleased_count"]),
                "snapshot_refreshed_at": (
                    str(inst["snapshot_refreshed_at"]) if inst["snapshot_refreshed_at"] else None
                ),
                "upgrade_enabled": bool(inst["upgrade_enabled"]),
                "upgrade_cooldown_days": int(inst["upgrade_cooldown_days"]),
            }
        )

    return JSONResponse({"instances": results, "recent_searches": recent})


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

"""Cooldown reconciliation against the *arr's live wanted sets.

A cooldown row is an operational decision made at dispatch time: Houndarr
just searched item X, so pause it for ``cooldown_days``.  That decision
remains valid only while the item is still something the *arr cares
about.  Items leave the *arr's wanted / upgrade-pool state for several
reasons: a download finished, the user unmonitored or deleted it, a file
crossed the quality cutoff, a series / artist / author was removed.  The
``cooldowns`` table never saw any of those events, so rows pile up and
inflate the dashboard's ``cooldown_breakdown``, driving Eligible below
its true value at the rollup scope.

This module reconciles that drift.  For each instance the supervisor
asks the adapter for the authoritative
:class:`~houndarr.clients.base.ReconcileSets` (leaf ids + synthetic
parent ids for context modes) at snapshot-refresh cadence, and this
function deletes every cooldown row whose ``(item_type, item_id)`` does
not appear in the matching ``search_kind`` set.  An empty
:class:`ReconcileSets` (e.g. a mid-fetch failure) is a hard skip: never
drive the DB to an empty state from an unreliable source.
"""

from __future__ import annotations

import logging

from houndarr.clients.base import ReconcileSets
from houndarr.database import get_db

logger = logging.getLogger(__name__)


async def reconcile_cooldowns(instance_id: int, sets: ReconcileSets) -> int:
    """Delete cooldown rows for *instance_id* not present in *sets*.

    Reads every cooldown row for the instance in one SELECT, filters
    in Python (cooldown tables are typically <5K rows per instance, so
    the set intersection is trivially fast), and DELETEs the
    non-matching rows in one batch.  Returns the count of rows removed
    for the supervisor to log.

    Args:
        instance_id: Primary key of the instance whose cooldowns should
            be reconciled.
        sets: Authoritative ``(item_type, item_id)`` sets per
            ``search_kind`` bucket.  Produced by the adapter's
            ``fetch_reconcile_sets`` method.  An empty sets object
            (every bucket empty) is treated as an explicit skip so a
            mid-fetch failure never wipes the table.

    Returns:
        Number of cooldown rows deleted.  Zero is the expected steady
        state on a healthy install.
    """
    if sets.is_empty():
        logger.debug(
            "cooldown reconcile skipped for instance %d: empty reconcile sets",
            instance_id,
        )
        return 0

    # Pull every cooldown row for the instance, classify each by its
    # stamped search_kind, and compare (item_type, item_id) against
    # the matching pass's wanted set.  Rows with an unrecognised
    # search_kind keep the column CHECK honest and are skipped from
    # reconciliation (they should never exist; defensive only).
    stale: list[int] = []
    async with get_db() as db:
        async with db.execute(
            """
            SELECT id, item_type, item_id, search_kind
              FROM cooldowns
             WHERE instance_id = ?
            """,
            (instance_id,),
        ) as cur:
            rows = list(await cur.fetchall())

        valid_by_kind = {
            "missing": sets.missing,
            "cutoff": sets.cutoff,
            "upgrade": sets.upgrade,
        }
        for row in rows:
            kind = str(row["search_kind"]) if row["search_kind"] else "missing"
            valid = valid_by_kind.get(kind)
            if valid is None:
                continue
            key = (str(row["item_type"]), int(row["item_id"]))
            if key not in valid:
                stale.append(int(row["id"]))

        if not stale:
            return 0

        # Delete stale rows in a single parameter-safe batch.  The
        # placeholder list is sized to the stale list so SQLite's
        # per-statement parameter limit (999 by default) is respected;
        # chunks of 500 stay well inside that bound.
        batch_size = 500
        for chunk_start in range(0, len(stale), batch_size):
            chunk = stale[chunk_start : chunk_start + batch_size]
            placeholders = ",".join("?" for _ in chunk)
            await db.execute(
                f"DELETE FROM cooldowns WHERE id IN ({placeholders})",  # noqa: S608  # nosec B608
                chunk,
            )
        await db.commit()

    logger.info(
        "reconciled cooldowns for instance %d: removed %d stale row(s) out of %d total",
        instance_id,
        len(stale),
        len(rows),
    )
    return len(stale)

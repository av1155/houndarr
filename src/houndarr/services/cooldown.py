"""Cooldown service: per-item search tracking and per-instance hourly cap.

The ``cooldowns`` table stores the last time each (instance, item) pair was
searched.  This module provides the four operations the search engine needs:

* :func:`is_on_cooldown` - should we skip this item?
* :func:`record_search` - mark an item as just-searched (upsert).
* :func:`count_searches_last_hour` - how many searches has this instance done
  in the past 60 minutes?
* :func:`clear_cooldowns` - admin reset for a single instance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from houndarr.database import get_db
from houndarr.engine.candidates import ItemType
from houndarr.value_objects import ItemRef


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """Format a datetime as the ISO-8601 string stored in SQLite."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def is_on_cooldown(
    instance_id: int,
    item_id: int,
    item_type: ItemType | str,
    cooldown_days: int,
) -> bool:
    """Return ``True`` if *item_id* was searched within *cooldown_days* days.

    Args:
        instance_id: Owning instance primary key.
        item_id: Item identifier (e.g. episode, movie, album, or book ID).
        item_type: ``"episode"``, ``"movie"``, ``"album"``, ``"book"``, or ``"whisparr_episode"``.
        cooldown_days: Number of days before the same item can be re-searched.
            Pass ``0`` to disable cooldowns entirely.

    Returns:
        ``True`` if a cooldown record exists and has not yet expired.
    """
    if cooldown_days <= 0:
        return False

    cutoff = _iso(_now_utc() - timedelta(days=cooldown_days))
    async with get_db() as db:
        async with db.execute(
            """
            SELECT 1 FROM cooldowns
            WHERE instance_id = ?
              AND item_id     = ?
              AND item_type   = ?
              AND searched_at > ?
            LIMIT 1
            """,
            (instance_id, item_id, item_type, cutoff),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def record_search(
    instance_id: int,
    item_id: int,
    item_type: ItemType | str,
    search_kind: str = "missing",
) -> None:
    """Upsert a cooldown record for *item_id* with the current UTC timestamp.

    If a record already exists for ``(instance_id, item_id, item_type)`` it is
    updated in place; otherwise a new row is inserted.

    Args:
        instance_id: Owning instance primary key.
        item_id: Item identifier (e.g. episode, movie, album, or book ID).
        item_type: ``"episode"``, ``"movie"``, ``"album"``, ``"book"``, or ``"whisparr_episode"``.
        search_kind: Which pass dispatched the search: ``"missing"``,
            ``"cutoff"``, or ``"upgrade"``.  Stamped on the cooldown
            row so the dashboard breakdown and the reconciliation path
            both read it from the column instead of re-classifying via
            ``search_log``.  Defaults to ``"missing"`` to keep older
            seed fixtures working.
    """
    now = _iso(_now_utc())
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at, search_kind)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instance_id, item_id, item_type)
            DO UPDATE SET searched_at = excluded.searched_at,
                          search_kind = excluded.search_kind
            """,
            (instance_id, item_id, item_type, now, search_kind),
        )
        await db.commit()


async def count_searches_last_hour(instance_id: int) -> int:
    """Return the number of searches recorded for *instance_id* in the last hour.

    Used by the search engine to enforce ``hourly_cap``.

    Args:
        instance_id: Owning instance primary key.

    Returns:
        Integer count (0 if none).
    """
    cutoff = _iso(_now_utc() - timedelta(hours=1))
    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM cooldowns
            WHERE instance_id = ?
              AND searched_at > ?
            """,
            (instance_id, cutoff),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def is_on_cooldown_ref(ref: ItemRef, cooldown_days: int) -> bool:
    """ItemRef-accepting overload of :func:`is_on_cooldown`."""
    return await is_on_cooldown(ref.instance_id, ref.item_id, ref.item_type, cooldown_days)


async def record_search_ref(ref: ItemRef, search_kind: str) -> None:
    """ItemRef-accepting overload of :func:`record_search`."""
    await record_search(ref.instance_id, ref.item_id, ref.item_type, search_kind)


async def clear_cooldowns(instance_id: int) -> int:
    """Delete all cooldown records for *instance_id*.

    Intended for the admin "reset cooldowns" action.

    Args:
        instance_id: Owning instance primary key.

    Returns:
        Number of rows deleted.
    """
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM cooldowns WHERE instance_id = ?",
            (instance_id,),
        )
        await db.commit()
        return cur.rowcount or 0

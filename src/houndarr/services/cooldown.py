"""Cooldown service: per-item search tracking and per-instance hourly cap.

The ``cooldowns`` table stores the last time each (instance, item) pair was
searched.  This module provides the four operations the search engine needs:

* :func:`is_on_cooldown` - should we skip this item?
* :func:`record_search` - mark an item as just-searched (upsert).
* :func:`count_searches_last_hour` - how many searches has this instance done
  in the past 60 minutes?
* :func:`clear_cooldowns` - admin reset for a single instance.

It also owns an in-memory LRU sentinel, :func:`should_log_skip`, that lets the
engine throttle duplicate cooldown-reason skip rows in ``search_log`` to at
most one per ``(instance_id, item_id, search_kind, reason_bucket)`` per 24 h.
Single-process only (the cache is module-level; multi-worker deploys would
reintroduce noise proportionally).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Literal

from houndarr.database import get_db

ItemType = Literal["episode", "movie", "album", "book", "whisparr_episode", "whisparr_v3_movie"]

# ---------------------------------------------------------------------------
# Skip-log throttle sentinel (single-process LRU with TTL)
# ---------------------------------------------------------------------------

SkipLogKey = tuple[int, int, str, str]

_SKIP_LOG_CACHE: OrderedDict[SkipLogKey, datetime] = OrderedDict()
_SKIP_LOG_MAX_ENTRIES = 1024
_SKIP_LOG_TTL = timedelta(hours=24)
_SKIP_LOG_LOCK = asyncio.Lock()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """Format a datetime as the ISO-8601 string stored in SQLite."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def is_on_cooldown(
    instance_id: int,
    item_id: int,
    item_type: ItemType,
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
    item_type: ItemType,
) -> None:
    """Upsert a cooldown record for *item_id* with the current UTC timestamp.

    If a record already exists for ``(instance_id, item_id, item_type)`` it is
    updated in place; otherwise a new row is inserted.

    Args:
        instance_id: Owning instance primary key.
        item_id: Item identifier (e.g. episode, movie, album, or book ID).
        item_type: ``"episode"``, ``"movie"``, ``"album"``, ``"book"``, or ``"whisparr_episode"``.
    """
    now = _iso(_now_utc())
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(instance_id, item_id, item_type)
            DO UPDATE SET searched_at = excluded.searched_at
            """,
            (instance_id, item_id, item_type, now),
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


async def should_log_skip(key: SkipLogKey) -> bool:
    """Gate duplicate skip-log writes for cooldown-reason skips.

    Engine passes write a ``search_log`` row every time an item is skipped
    for the same reason on every cycle.  On a healthy install that produces
    hundreds of identical rows per item per cooldown window.  This sentinel
    caps writes to at most one per key per 24 h.

    The check-and-write is serialized under an :class:`asyncio.Lock` so two
    concurrent passes racing on the same item cannot both bypass the cache.
    The cache is bounded at ``_SKIP_LOG_MAX_ENTRIES`` entries with LRU
    eviction, and TTL is enforced on read.

    Args:
        key: ``(instance_id, item_id, search_kind, reason_bucket)``.
            ``reason_bucket`` is a coarse category string, e.g.
            ``"cooldown"``, ``"cutoff_cd"``, ``"upgrade_cd"``.

    Returns:
        ``True`` if the caller should write the skip row (cache miss or
        expired entry).  ``False`` if a row for the same key was recorded
        within the last 24 h.
    """
    now = datetime.now(UTC)
    async with _SKIP_LOG_LOCK:
        entry = _SKIP_LOG_CACHE.get(key)
        if entry is not None and now - entry < _SKIP_LOG_TTL:
            _SKIP_LOG_CACHE.move_to_end(key)
            return False
        _SKIP_LOG_CACHE[key] = now
        _SKIP_LOG_CACHE.move_to_end(key)
        while len(_SKIP_LOG_CACHE) > _SKIP_LOG_MAX_ENTRIES:
            _SKIP_LOG_CACHE.popitem(last=False)
        return True


def _reset_skip_log_cache() -> None:
    """Clear the sentinel cache.  Test-only helper."""
    _SKIP_LOG_CACHE.clear()

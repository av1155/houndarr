"""Cooldown service: per-item search tracking and skip-log throttling.

The ``cooldowns`` table stores the last time each (instance, item) pair was
searched.  This module provides three operations the search engine needs:

* :func:`is_on_cooldown_ref` - should we skip this item?
* :func:`record_search_ref` - mark an item as just-searched (upsert).
* :func:`clear_cooldowns` - admin reset for a single instance.

The ``_ref`` variants take an :class:`~houndarr.value_objects.ItemRef` and
own the SQL implementation; the positional :func:`is_on_cooldown` and
:func:`record_search` are compat shims kept around for test seeds and any
caller that predates the :class:`ItemRef` migration.  New code should
build an :class:`ItemRef` and call the ``_ref`` form directly.

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

from houndarr.database import get_db
from houndarr.engine.candidates import ItemType
from houndarr.value_objects import ItemRef

# Skip-log throttle sentinel (single-process LRU with TTL)

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


async def is_on_cooldown_ref(ref: ItemRef, cooldown_days: int) -> bool:
    """Return ``True`` if *ref* was searched within *cooldown_days* days.

    Source of truth for the cooldown-lookup SQL.  The positional form
    :func:`is_on_cooldown` is a thin compat shim around this function.

    Args:
        ref: The item to check.
        cooldown_days: Number of days before the same item can be
            re-searched.  Pass ``0`` (or any non-positive value) to
            disable cooldowns entirely; the function short-circuits to
            ``False`` and performs no DB read in that case.

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
            (ref.instance_id, ref.item_id, ref.item_type.value, cutoff),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def record_search_ref(ref: ItemRef) -> None:
    """Upsert a cooldown record for *ref* with the current UTC timestamp.

    Source of truth for the cooldown-upsert SQL.  If a record already
    exists for ``(ref.instance_id, ref.item_id, ref.item_type)`` it is
    updated in place; otherwise a new row is inserted.  The positional
    form :func:`record_search` is a thin compat shim around this
    function.

    Args:
        ref: The item to record as just-searched.
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
            (ref.instance_id, ref.item_id, ref.item_type.value, now),
        )
        await db.commit()


async def is_on_cooldown(
    instance_id: int,
    item_id: int,
    item_type: ItemType | str,
    cooldown_days: int,
) -> bool:
    """Positional compat wrapper over :func:`is_on_cooldown_ref`.

    Retained so test fixtures and seed helpers that predate the
    :class:`ItemRef` migration can keep calling the three-positional-arg
    form.  New engine code should build an :class:`ItemRef` and call
    :func:`is_on_cooldown_ref` directly.

    Args:
        instance_id: Owning instance primary key.
        item_id: Item identifier (e.g. episode, movie, album, or book
            ID).
        item_type: ``"episode"``, ``"movie"``, ``"album"``, ``"book"``,
            ``"whisparr_episode"``, or ``"whisparr_v3_movie"``.  Plain
            ``str`` values are coerced to :class:`ItemType` for the
            ItemRef construction.
        cooldown_days: Number of days before the same item can be
            re-searched.

    Returns:
        ``True`` if a cooldown record exists and has not yet expired.
    """
    return await is_on_cooldown_ref(
        ItemRef(instance_id, item_id, ItemType(item_type)),
        cooldown_days,
    )


async def record_search(
    instance_id: int,
    item_id: int,
    item_type: ItemType | str,
) -> None:
    """Positional compat wrapper over :func:`record_search_ref`.

    Retained for the same reason as :func:`is_on_cooldown`.  New engine
    code should build an :class:`ItemRef` and call
    :func:`record_search_ref` directly.

    Args:
        instance_id: Owning instance primary key.
        item_id: Item identifier (e.g. episode, movie, album, or book
            ID).
        item_type: ``"episode"``, ``"movie"``, ``"album"``, ``"book"``,
            ``"whisparr_episode"``, or ``"whisparr_v3_movie"``.  Plain
            ``str`` values are coerced to :class:`ItemType` for the
            ItemRef construction.
    """
    await record_search_ref(
        ItemRef(instance_id, item_id, ItemType(item_type)),
    )


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

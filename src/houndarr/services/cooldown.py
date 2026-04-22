"""Cooldown service: per-item search tracking and skip-log throttling.

The ``cooldowns`` table SQL lives in
:mod:`houndarr.repositories.cooldowns`; this module is the
service-layer facade over that boundary and retains exclusive
ownership of the in-memory LRU throttle (:func:`should_log_skip`)
that gates duplicate cooldown-reason skip rows in ``search_log``.
The throttle is single-process state, not SQL, so it does not
belong in a repository.

Public surface:

* :func:`is_on_cooldown_ref` and :func:`record_search_ref` are thin
  delegators over the repository.  They are the canonical API; new
  engine code should build an :class:`~houndarr.value_objects.ItemRef`
  and call them.
* :func:`is_on_cooldown` and :func:`record_search` are positional
  compat shims kept for test seeds and any caller that still uses
  the positional triple directly.
* :func:`clear_cooldowns` delegates the admin reset to the repository.
* :func:`should_log_skip` owns the LRU sentinel.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from houndarr.engine.candidates import ItemType
from houndarr.value_objects import ItemRef

# Skip-log throttle sentinel (single-process LRU with TTL)

SkipLogKey = tuple[int, int, str, str]

_SKIP_LOG_CACHE: OrderedDict[SkipLogKey, datetime] = OrderedDict()
_SKIP_LOG_MAX_ENTRIES = 1024
_SKIP_LOG_TTL = timedelta(hours=24)
_SKIP_LOG_LOCK = asyncio.Lock()


async def is_on_cooldown_ref(ref: ItemRef, cooldown_days: int) -> bool:
    """Return ``True`` if *ref* was searched within *cooldown_days* days.

    Thin delegator over
    :func:`houndarr.repositories.cooldowns.exists_active_cooldown`;
    the SQL lives in the repository and this module stays in place
    as the stable import path for engine callers.

    Args:
        ref: The item to check.
        cooldown_days: Number of days before the same item can be
            re-searched.  Pass ``0`` (or any non-positive value) to
            disable cooldowns entirely; the underlying repository
            call short-circuits without touching the database.

    Returns:
        ``True`` if a cooldown record exists and has not yet expired.
    """
    from houndarr.repositories.cooldowns import exists_active_cooldown

    return await exists_active_cooldown(ref, cooldown_days)


async def record_search_ref(ref: ItemRef, search_kind: str) -> None:
    """Upsert a cooldown record for *ref* with the current UTC timestamp.

    Thin delegator over
    :func:`houndarr.repositories.cooldowns.upsert_cooldown`.

    Args:
        ref: The item to record as just-searched.
        search_kind: Which pass dispatched the search: ``"missing"``,
            ``"cutoff"``, or ``"upgrade"``.  Forwarded verbatim so the
            cooldown row carries the classification for later dashboard
            breakdown reads and for the reconciliation path that
            intersects cooldowns against each pass's wanted set.
    """
    from houndarr.repositories.cooldowns import upsert_cooldown

    await upsert_cooldown(ref, search_kind)


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
            ``"whisparr_v2_episode"``, or ``"whisparr_v3_movie"``.  Plain
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
    search_kind: str = "missing",
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
            ``"whisparr_v2_episode"``, or ``"whisparr_v3_movie"``.  Plain
            ``str`` values are coerced to :class:`ItemType` for the
            ItemRef construction.
        search_kind: Defaults to ``"missing"`` to preserve the old
            positional signature for seed fixtures and tests that
            predate the classified-cooldown migration.  Pass
            ``"cutoff"`` or ``"upgrade"`` explicitly when seeding
            those cases.
    """
    await record_search_ref(
        ItemRef(instance_id, item_id, ItemType(item_type)),
        search_kind,
    )


async def count_searches_last_hour(instance_id: int) -> int:
    """Return how many cooldown rows *instance_id* has from the past 60 minutes.

    Counts ``cooldowns`` rows whose ``searched_at`` falls inside the
    rolling one-hour window.  Used by the engine's per-instance hourly
    cap.  Lives here as inline SQL alongside the
    :func:`record_search` / :func:`is_on_cooldown` positional shims;
    the cooldowns reads it shares with them keep the inline helpers
    grouped on one module so the engine has a single import path.
    """
    from houndarr.database import get_db

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


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """ISO-8601 with millisecond precision and trailing Z, matching SQL writes."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def clear_cooldowns(instance_id: int) -> int:
    """Delete all cooldown records for *instance_id*.

    Thin delegator over
    :func:`houndarr.repositories.cooldowns.delete_cooldowns_for_instance`.
    Intended for the admin "reset cooldowns" action.

    Args:
        instance_id: Owning instance primary key.

    Returns:
        Number of rows deleted.
    """
    from houndarr.repositories.cooldowns import delete_cooldowns_for_instance

    return await delete_cooldowns_for_instance(instance_id)


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


# Info-row throttle (separate cache + caller-supplied TTL)

InfoLogKey = tuple[int, str]
"""``(instance_id, reason_key)``.  The ``reason_key`` is a short
stable slug identifying which recurring info-row this entry gates
(e.g. ``"upgrade_pool_empty"``).  Keys are intentionally coarse: one
per-instance-per-condition, not per-cycle."""

_INFO_LOG_CACHE: OrderedDict[InfoLogKey, datetime] = OrderedDict()
_INFO_LOG_MAX_ENTRIES = 256
_INFO_LOG_LOCK = asyncio.Lock()


async def should_log_info(key: InfoLogKey, ttl_seconds: int) -> bool:
    """Gate recurring cycle-level info rows behind a caller-supplied TTL.

    Sibling to :func:`should_log_skip` for info-row scenarios where a
    condition persists across many cycles and would otherwise write an
    identical row per cycle.  The upgrade-pass-empty case on an
    instance with no upgrade-eligible items produces one row per
    cycle; at a five-minute interval that is 288 rows per day per
    instance of pure noise.  Wrapping the write behind
    ``should_log_info((instance_id, "upgrade_pool_empty"), 6*3600)``
    caps it at one row per six-hour window per instance.

    The check-and-write is serialized under a dedicated lock so two
    concurrent passes cannot both bypass the cache.  Cache is bounded
    at 256 entries with LRU eviction; the keyspace is small (roughly
    one entry per instance per distinct info-row category) so the
    cap almost never fires in practice.

    Args:
        key: ``(instance_id, reason_key)``.  ``reason_key`` is a
            stable short slug describing the info-row kind.
        ttl_seconds: How long to suppress duplicate rows for the same
            key, in seconds.  Caller owns the cadence; this function
            does not pick one.

    Returns:
        ``True`` if the caller should write the info row (cache miss
        or expired entry).  ``False`` if a row for the same key was
        recorded within the TTL window.
    """
    now = datetime.now(UTC)
    ttl = timedelta(seconds=ttl_seconds)
    async with _INFO_LOG_LOCK:
        entry = _INFO_LOG_CACHE.get(key)
        if entry is not None and now - entry < ttl:
            _INFO_LOG_CACHE.move_to_end(key)
            return False
        _INFO_LOG_CACHE[key] = now
        _INFO_LOG_CACHE.move_to_end(key)
        while len(_INFO_LOG_CACHE) > _INFO_LOG_MAX_ENTRIES:
            _INFO_LOG_CACHE.popitem(last=False)
        return True


def _reset_info_log_cache() -> None:
    """Clear the info-log sentinel cache.  Test-only helper."""
    _INFO_LOG_CACHE.clear()

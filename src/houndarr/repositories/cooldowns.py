"""Cooldowns aggregate: SQL boundary for the ``cooldowns`` table.

Three functions cover the full surface:

- :func:`exists_active_cooldown`: the SELECT that decides whether
  an item is still inside its cooldown window.
- :func:`upsert_cooldown`: the ``INSERT ... ON CONFLICT ... DO
  UPDATE`` that records a freshly-searched item.
- :func:`delete_cooldowns_for_instance`: the admin "reset cooldowns"
  cascade for a single instance.

The in-memory LRU sentinel (``should_log_skip`` and
``_reset_skip_log_cache``) stays in the service module: it guards
log writes, not the cooldowns table.  The repository is
function-based with no class.

Timestamps are serialised in the SQLite-native ISO-8601 format
(``strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'``) so ``searched_at``
values compare lexicographically against both existing rows and
fresh writes.  The format string is duplicated here intentionally:
the ``search_log`` table uses a slightly different format
(``%f`` is milliseconds there) and mixing the two is a debugging
footgun when the column names are the same shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from houndarr.database import get_db
from houndarr.value_objects import ItemRef


def _now_utc() -> datetime:
    """Return the current UTC moment.

    Wrapped in a helper so tests can ``monkeypatch`` time without
    touching every SQL call site.  Used by both :func:`exists_active_cooldown`
    (cutoff computation) and :func:`upsert_cooldown` (timestamp write).
    """
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """Format a datetime in the cooldowns column's storage format.

    The output is lexicographically comparable against existing
    ``searched_at`` values stored in the same format.

    Args:
        dt: Datetime to render.  The caller is responsible for
            passing a UTC value; no timezone conversion is applied.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def exists_active_cooldown(ref: ItemRef, cooldown_days: int) -> bool:
    """Return ``True`` when *ref* is still inside its cooldown window.

    Short-circuits to ``False`` without touching the database when
    *cooldown_days* is zero or negative: disabling cooldowns is a
    common config, so the cheap path stays cheap.  Otherwise the
    cutoff is ``now - cooldown_days`` rendered with :func:`_iso`,
    and a row is active iff its ``searched_at`` sits strictly after
    the cutoff.

    Args:
        ref: The (instance, item_id, item_type) triple to check.
        cooldown_days: Length of the cooldown window in days.  Pass
            a non-positive value to disable.
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


async def upsert_cooldown(ref: ItemRef, search_kind: str) -> None:
    """Record *ref* as just-searched, upserting any existing row.

    Uses ``INSERT ... ON CONFLICT(instance_id, item_id, item_type) DO
    UPDATE SET searched_at = excluded.searched_at, search_kind =
    excluded.search_kind`` so repeated searches slide both the
    timestamp AND the classification forward.  One row per
    (instance_id, item_id, item_type) carries the kind of the most
    recent search that wrote it; the reconciliation path reads that
    column directly instead of re-deriving it from search_log.

    Args:
        ref: The (instance, item_id, item_type) triple that was just
            searched.  ``item_type`` serialises through
            :attr:`~enum.StrEnum.value`.
        search_kind: Which pass dispatched the search: ``"missing"``,
            ``"cutoff"``, or ``"upgrade"``.  Required, non-default on
            purpose: callers always know which pass they are in, and
            a default would silently miscategorise cutoff / upgrade
            searches as missing.  The DB CHECK constraint enforces
            the allowed values; passing anything else raises at
            write time.
    """
    now = _iso(_now_utc())
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instance_id, item_id, item_type)
            DO UPDATE SET
                searched_at = excluded.searched_at,
                search_kind = excluded.search_kind
            """,
            (ref.instance_id, ref.item_id, ref.item_type.value, search_kind, now),
        )
        await db.commit()


async def delete_cooldowns_for_instance(instance_id: int) -> int:
    """Delete every cooldown row for *instance_id*.

    Used by the admin "reset cooldowns" action.  FK-cascaded deletes
    from the instance row are handled at the schema level; this
    function is for the explicit per-instance reset that leaves the
    instance row itself intact.

    Args:
        instance_id: Owning instance primary key.

    Returns:
        Number of rows removed.  ``0`` is valid (the instance never
        had any cooldown records).
    """
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM cooldowns WHERE instance_id = ?",
            (instance_id,),
        )
        await db.commit()
        return cur.rowcount or 0

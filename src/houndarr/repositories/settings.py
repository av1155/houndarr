"""Key-value settings aggregate: SQL boundary for the ``settings`` table.

Track D.2 introduces this module.  The previous incarnation lived as
two helpers (``get_setting`` / ``set_setting``) inside
:mod:`houndarr.database`; this repository owns the SQL now and the
database-module wrappers delegate here so callers migrate one file
at a time.  ``delete_setting`` is added to match the full
:class:`houndarr.protocols.SettingsRepository` contract; the previous
code path had no delete at all because removing a setting was never
required by any caller, but the contract calls for the symmetry.

Function shape matches the Protocol (no ``default`` argument on
``get_setting``).  Callers that need a default fall back at their own
call site: ``(await get_setting(key)) or "default"`` for the truthy
case, or ``(await get_setting(key)) if value is not None else default``
for the strict ``None``-only fallback.  The legacy
:func:`houndarr.database.get_setting` wrapper preserves the explicit
``default=`` kwarg for the route call sites that still import from
there; later D batches thin those callers out.
"""

from __future__ import annotations

from houndarr.database import get_db


async def get_setting(key: str) -> str | None:
    """Fetch a single setting value by key.

    Args:
        key: Setting key to look up in the ``settings`` table.

    Returns:
        The stored value as a string, or ``None`` if no row exists for
        *key*.  Callers that want a default on a missing row must
        handle the ``None`` branch at the call site; the repository
        contract deliberately omits the default to keep the SQL
        boundary minimal.
    """
    async with get_db() as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return str(row["value"]) if row else None


async def set_setting(key: str, value: str) -> None:
    """Upsert a single setting row.

    Executes ``INSERT ... ON CONFLICT(key) DO UPDATE SET value =
    excluded.value`` so the row is either created or rewritten.  The
    caller is responsible for any value serialisation (JSON, epoch
    seconds, etc.); the column is ``TEXT`` and stores the string
    verbatim.

    Args:
        key: Setting key.
        value: Raw string value to store.  Empty strings are stored
            as empty strings (distinct from a missing row, which
            yields ``None`` from :func:`get_setting`).
    """
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def delete_setting(key: str) -> None:
    """Delete a setting row if it exists.

    Silently succeeds when the key is absent: the intent is idempotent
    removal.  No caller relies on the number of rows affected today,
    so the function returns ``None`` rather than a count; add a
    ``rowcount``-returning variant if a future caller needs it.

    Args:
        key: Setting key to remove.
    """
    async with get_db() as db:
        await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        await db.commit()

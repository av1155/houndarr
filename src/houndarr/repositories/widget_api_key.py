"""Repository functions for the single Houndarr widget API key."""

from __future__ import annotations

import re

from houndarr.database import get_db
from houndarr.value_objects import WidgetApiKey

_HASH_RE = re.compile(r"[0-9a-f]{64}")


async def get() -> WidgetApiKey | None:
    """Return the stored widget API key metadata, if one exists."""
    async with get_db() as db:
        async with db.execute(
            "SELECT hash, created_at, last_used_at FROM widget_api_key WHERE id = 1"
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return WidgetApiKey(
        hash=row["hash"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )


async def set(key_hash: str) -> WidgetApiKey:
    """Store *key_hash* as the active widget API key and return its metadata."""
    if _HASH_RE.fullmatch(key_hash) is None:
        raise ValueError("widget API key hash must be a SHA-256 hex digest")

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO widget_api_key (id, hash, created_at, last_used_at)
            VALUES (1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), NULL)
            ON CONFLICT(id) DO UPDATE SET
                hash = excluded.hash,
                created_at = excluded.created_at,
                last_used_at = NULL
            """,
            (key_hash,),
        )
        await db.commit()
    stored = await get()
    if stored is None:
        raise RuntimeError("widget API key write did not persist")
    return stored


async def touch_last_used() -> None:
    """Mark the active widget API key as used at the current UTC time."""
    async with get_db() as db:
        await db.execute(
            """
            UPDATE widget_api_key
            SET last_used_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = 1
            """
        )
        await db.commit()


async def revoke() -> None:
    """Delete the active widget API key if one exists."""
    async with get_db() as db:
        await db.execute("DELETE FROM widget_api_key WHERE id = 1")
        await db.commit()

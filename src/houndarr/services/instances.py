"""Instance CRUD service — create, read, update, and delete *arr instances.

API keys are never stored in plaintext.  Every write encrypts with the
caller-supplied Fernet *master_key*; every read decrypts transparently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from houndarr.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_BATCH_SIZE,
    DEFAULT_HOURLY_CAP,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_UNRELEASED_DELAY_HOURS,
)
from houndarr.crypto import decrypt, encrypt
from houndarr.database import get_db


class InstanceType(StrEnum):
    """Supported *arr application types."""

    sonarr = "sonarr"
    radarr = "radarr"


@dataclass
class Instance:
    """In-memory representation of a configured *arr instance.

    ``api_key`` is always the **decrypted** plaintext value — the encrypted
    form is only ever kept in the database column ``encrypted_api_key``.
    """

    id: int
    name: str
    type: InstanceType
    url: str
    api_key: str
    enabled: bool
    batch_size: int
    sleep_interval_mins: int
    hourly_cap: int
    cooldown_days: int
    unreleased_delay_hrs: int
    cutoff_enabled: bool
    cutoff_batch_size: int
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_instance(row: Any, master_key: bytes) -> Instance:
    """Convert an aiosqlite Row to an :class:`Instance`, decrypting the key."""
    return Instance(
        id=row["id"],
        name=row["name"],
        type=InstanceType(row["type"]),
        url=row["url"],
        api_key=decrypt(row["encrypted_api_key"], master_key),
        enabled=bool(row["enabled"]),
        batch_size=row["batch_size"],
        sleep_interval_mins=row["sleep_interval_mins"],
        hourly_cap=row["hourly_cap"],
        cooldown_days=row["cooldown_days"],
        unreleased_delay_hrs=row["unreleased_delay_hrs"],
        cutoff_enabled=bool(row["cutoff_enabled"]),
        cutoff_batch_size=row["cutoff_batch_size"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_instance(
    *,
    master_key: bytes,
    name: str,
    type: InstanceType,  # noqa: A002
    url: str,
    api_key: str,
    enabled: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: int = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: int = DEFAULT_HOURLY_CAP,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    unreleased_delay_hrs: int = DEFAULT_UNRELEASED_DELAY_HOURS,
    cutoff_enabled: bool = False,
    cutoff_batch_size: int = DEFAULT_CUTOFF_BATCH_SIZE,
) -> Instance:
    """Insert a new instance row and return the populated :class:`Instance`.

    Args:
        master_key: Fernet key used to encrypt *api_key* before storage.
        name: Human-readable label for the instance.
        type: ``sonarr`` or ``radarr``.
        url: Base URL of the *arr instance (e.g. ``http://sonarr:8989``).
        api_key: Plaintext API key — will be encrypted before being written.
        enabled: Whether the search engine should process this instance.
        batch_size: Number of missing items to search per run.
        sleep_interval_mins: Minutes to sleep between search cycles.
        hourly_cap: Maximum searches allowed per hour.
        cooldown_days: Days to wait before re-searching the same item.
        unreleased_delay_hrs: Hours to wait after an item's air date.
        cutoff_enabled: Whether cutoff-unmet searching is active.
        cutoff_batch_size: Number of cutoff-unmet items per run.

    Returns:
        The newly created :class:`Instance` with its database-assigned *id*.
    """
    encrypted = encrypt(api_key, master_key)
    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO instances (
                name, type, url, encrypted_api_key,
                enabled, batch_size, sleep_interval_mins,
                hourly_cap, cooldown_days, unreleased_delay_hrs,
                cutoff_enabled, cutoff_batch_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                type.value,
                url,
                encrypted,
                int(enabled),
                batch_size,
                sleep_interval_mins,
                hourly_cap,
                cooldown_days,
                unreleased_delay_hrs,
                int(cutoff_enabled),
                cutoff_batch_size,
            ),
        )
        await db.commit()
        row_id = cur.lastrowid
        assert row_id is not None  # noqa: S101

    instance = await get_instance(row_id, master_key=master_key)
    assert instance is not None  # just inserted — cannot be None  # noqa: S101
    return instance


async def get_instance(id: int, *, master_key: bytes) -> Instance | None:  # noqa: A002
    """Fetch a single instance by *id*, or ``None`` if not found.

    Args:
        id: Primary key of the instance row.
        master_key: Fernet key used to decrypt the stored API key.

    Returns:
        Decrypted :class:`Instance`, or ``None``.
    """
    async with get_db() as db:
        async with db.execute("SELECT * FROM instances WHERE id = ?", (id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_instance(row, master_key)


async def list_instances(*, master_key: bytes) -> list[Instance]:
    """Return all instances ordered by creation time (oldest first).

    Args:
        master_key: Fernet key used to decrypt each stored API key.

    Returns:
        List of decrypted :class:`Instance` objects (may be empty).
    """
    async with get_db() as db:
        async with db.execute("SELECT * FROM instances ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [_row_to_instance(r, master_key) for r in rows]


async def update_instance(
    id: int,  # noqa: A002
    *,
    master_key: bytes,
    **fields: Any,
) -> Instance | None:
    """Partially update an instance and return the refreshed :class:`Instance`.

    Accepts any subset of the mutable columns.  If ``api_key`` is included it
    is re-encrypted before being persisted.  Unrecognised field names are
    silently ignored to avoid SQL injection.

    Args:
        id: Primary key of the instance to update.
        master_key: Fernet key (needed for re-encryption and for the return value).
        **fields: Column-value pairs to update.  Accepted keys:
            ``name``, ``type``, ``url``, ``api_key``, ``enabled``,
            ``batch_size``, ``sleep_interval_mins``, ``hourly_cap``,
            ``cooldown_days``, ``unreleased_delay_hrs``,
            ``cutoff_enabled``, ``cutoff_batch_size``.

    Returns:
        Updated :class:`Instance`, or ``None`` if *id* does not exist.
    """
    # Map public field names → DB column names
    allowed_cols: dict[str, str] = {
        "name": "name",
        "type": "type",
        "url": "url",
        "api_key": "encrypted_api_key",
        "enabled": "enabled",
        "batch_size": "batch_size",
        "sleep_interval_mins": "sleep_interval_mins",
        "hourly_cap": "hourly_cap",
        "cooldown_days": "cooldown_days",
        "unreleased_delay_hrs": "unreleased_delay_hrs",
        "cutoff_enabled": "cutoff_enabled",
        "cutoff_batch_size": "cutoff_batch_size",
    }

    assignments: list[str] = []
    values: list[Any] = []

    for field_name, value in fields.items():
        col = allowed_cols.get(field_name)
        if col is None:
            continue
        # Coerce types for SQLite
        if field_name == "api_key":
            value = encrypt(str(value), master_key)
        elif field_name == "type" and isinstance(value, InstanceType):
            value = value.value
        elif field_name in ("enabled", "cutoff_enabled"):
            value = int(bool(value))
        assignments.append(f"{col} = ?")
        values.append(value)

    if not assignments:
        # Nothing to do — return current state
        return await get_instance(id, master_key=master_key)

    # Always refresh updated_at
    assignments.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    values.append(id)

    sql = f"UPDATE instances SET {', '.join(assignments)} WHERE id = ?"  # noqa: S608  # nosec B608
    async with get_db() as db:
        await db.execute(sql, values)
        await db.commit()

    return await get_instance(id, master_key=master_key)


async def delete_instance(id: int) -> bool:  # noqa: A002
    """Delete an instance row (cascade removes cooldowns).

    Args:
        id: Primary key of the instance to delete.

    Returns:
        ``True`` if a row was deleted, ``False`` if *id* did not exist.
    """
    async with get_db() as db:
        cur = await db.execute("DELETE FROM instances WHERE id = ?", (id,))
        await db.commit()
        return (cur.rowcount or 0) > 0

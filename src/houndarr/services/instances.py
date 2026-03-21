"""Instance CRUD service: create, read, update, and delete *arr instances.

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
    DEFAULT_CUTOFF_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_HOURLY_CAP,
    DEFAULT_HOURLY_CAP,
    DEFAULT_LIDARR_SEARCH_MODE,
    DEFAULT_POST_RELEASE_GRACE_HOURS,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_READARR_SEARCH_MODE,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_BATCH_SIZE,
    DEFAULT_UPGRADE_COOLDOWN_DAYS,
    DEFAULT_UPGRADE_HOURLY_CAP,
    DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE,
    DEFAULT_WHISPARR_SEARCH_MODE,
)
from houndarr.crypto import decrypt, encrypt
from houndarr.database import get_db


class InstanceType(StrEnum):
    """Supported *arr application types."""

    radarr = "radarr"
    sonarr = "sonarr"
    lidarr = "lidarr"
    readarr = "readarr"
    whisparr = "whisparr"


class SonarrSearchMode(StrEnum):
    """Supported Sonarr missing-search strategies."""

    episode = "episode"
    season_context = "season_context"


class LidarrSearchMode(StrEnum):
    """Supported Lidarr missing-search strategies."""

    album = "album"
    artist_context = "artist_context"


class ReadarrSearchMode(StrEnum):
    """Supported Readarr missing-search strategies."""

    book = "book"
    author_context = "author_context"


class WhisparrSearchMode(StrEnum):
    """Supported Whisparr missing-search strategies."""

    episode = "episode"
    season_context = "season_context"


@dataclass
class Instance:
    """In-memory representation of a configured *arr instance.

    ``api_key`` is always the **decrypted** plaintext value; the encrypted
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
    post_release_grace_hrs: int
    queue_limit: int
    cutoff_enabled: bool
    cutoff_batch_size: int
    cutoff_cooldown_days: int
    cutoff_hourly_cap: int
    created_at: str
    updated_at: str
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode
    upgrade_enabled: bool = False
    upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE
    upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS
    upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book
    upgrade_whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode
    upgrade_item_offset: int = 0
    upgrade_series_offset: int = 0


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
        post_release_grace_hrs=row["post_release_grace_hrs"],
        queue_limit=row["queue_limit"],
        cutoff_enabled=bool(row["cutoff_enabled"]),
        cutoff_batch_size=row["cutoff_batch_size"],
        cutoff_cooldown_days=row["cutoff_cooldown_days"],
        cutoff_hourly_cap=row["cutoff_hourly_cap"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sonarr_search_mode=SonarrSearchMode(row["sonarr_search_mode"]),
        lidarr_search_mode=LidarrSearchMode(row["lidarr_search_mode"]),
        readarr_search_mode=ReadarrSearchMode(row["readarr_search_mode"]),
        whisparr_search_mode=WhisparrSearchMode(row["whisparr_search_mode"]),
        upgrade_enabled=bool(row["upgrade_enabled"]),
        upgrade_batch_size=row["upgrade_batch_size"],
        upgrade_cooldown_days=row["upgrade_cooldown_days"],
        upgrade_hourly_cap=row["upgrade_hourly_cap"],
        upgrade_sonarr_search_mode=SonarrSearchMode(row["upgrade_sonarr_search_mode"]),
        upgrade_lidarr_search_mode=LidarrSearchMode(row["upgrade_lidarr_search_mode"]),
        upgrade_readarr_search_mode=ReadarrSearchMode(row["upgrade_readarr_search_mode"]),
        upgrade_whisparr_search_mode=WhisparrSearchMode(row["upgrade_whisparr_search_mode"]),
        upgrade_item_offset=row["upgrade_item_offset"],
        upgrade_series_offset=row["upgrade_series_offset"],
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
    post_release_grace_hrs: int = DEFAULT_POST_RELEASE_GRACE_HOURS,
    queue_limit: int = DEFAULT_QUEUE_LIMIT,
    cutoff_enabled: bool = False,
    cutoff_batch_size: int = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: int = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: int = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE),
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE),
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE),
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode(DEFAULT_WHISPARR_SEARCH_MODE),
    upgrade_enabled: bool = False,
    upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE,
    upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS,
    upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP,
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(
        DEFAULT_UPGRADE_SONARR_SEARCH_MODE
    ),
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(
        DEFAULT_UPGRADE_LIDARR_SEARCH_MODE
    ),
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(
        DEFAULT_UPGRADE_READARR_SEARCH_MODE
    ),
    upgrade_whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode(
        DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE
    ),
) -> Instance:
    """Insert a new instance row and return the populated :class:`Instance`.

    Args:
        master_key: Fernet key used to encrypt *api_key* before storage.
        name: Human-readable label for the instance.
        type: One of ``radarr``, ``sonarr``, ``lidarr``, ``readarr``, ``whisparr``.
        url: Base URL of the *arr instance (e.g. ``http://sonarr:8989``).
        api_key: Plaintext API key; will be encrypted before being written.
        enabled: Whether the search engine should process this instance.
        batch_size: Number of missing items to search per run.
        sleep_interval_mins: Minutes to sleep between search cycles.
        hourly_cap: Maximum searches allowed per hour.
        cooldown_days: Days to wait before re-searching the same item.
        post_release_grace_hrs: Hours to wait after release before searching.
        queue_limit: Skip search cycles when the download queue exceeds
            this count.  ``0`` disables the check.
        cutoff_enabled: Whether cutoff-unmet searching is active.
        cutoff_batch_size: Number of cutoff-unmet items per run.
        cutoff_cooldown_days: Days to wait before re-searching cutoff-unmet items.
        cutoff_hourly_cap: Maximum cutoff searches allowed per hour.
        sonarr_search_mode: Sonarr missing-search strategy mode.
        lidarr_search_mode: Lidarr missing-search strategy mode.
        readarr_search_mode: Readarr missing-search strategy mode.
        whisparr_search_mode: Whisparr missing-search strategy mode.
        upgrade_enabled: Whether upgrade searching is active.
        upgrade_batch_size: Number of upgrade items per run.
        upgrade_cooldown_days: Days to wait before re-searching upgrade items.
        upgrade_hourly_cap: Maximum upgrade searches allowed per hour.
        upgrade_sonarr_search_mode: Sonarr upgrade-search strategy mode.
        upgrade_lidarr_search_mode: Lidarr upgrade-search strategy mode.
        upgrade_readarr_search_mode: Readarr upgrade-search strategy mode.
        upgrade_whisparr_search_mode: Whisparr upgrade-search strategy mode.

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
                hourly_cap, cooldown_days, post_release_grace_hrs, queue_limit,
                cutoff_enabled, cutoff_batch_size, cutoff_cooldown_days, cutoff_hourly_cap,
                sonarr_search_mode, lidarr_search_mode, readarr_search_mode,
                whisparr_search_mode,
                upgrade_enabled, upgrade_batch_size, upgrade_cooldown_days,
                upgrade_hourly_cap,
                upgrade_sonarr_search_mode, upgrade_lidarr_search_mode,
                upgrade_readarr_search_mode, upgrade_whisparr_search_mode
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
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
                post_release_grace_hrs,
                queue_limit,
                int(cutoff_enabled),
                cutoff_batch_size,
                cutoff_cooldown_days,
                cutoff_hourly_cap,
                sonarr_search_mode.value,
                lidarr_search_mode.value,
                readarr_search_mode.value,
                whisparr_search_mode.value,
                int(upgrade_enabled),
                upgrade_batch_size,
                upgrade_cooldown_days,
                upgrade_hourly_cap,
                upgrade_sonarr_search_mode.value,
                upgrade_lidarr_search_mode.value,
                upgrade_readarr_search_mode.value,
                upgrade_whisparr_search_mode.value,
            ),
        )
        await db.commit()
        row_id = cur.lastrowid
        assert row_id is not None  # noqa: S101

    instance = await get_instance(row_id, master_key=master_key)
    assert instance is not None  # just inserted, cannot be None  # noqa: S101
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
            ``cooldown_days``, ``post_release_grace_hrs``, ``queue_limit``,
            ``cutoff_enabled``, ``cutoff_batch_size``,
            ``cutoff_cooldown_days``, ``cutoff_hourly_cap``,
            ``sonarr_search_mode``, ``lidarr_search_mode``,
            ``readarr_search_mode``, ``whisparr_search_mode``,
            ``upgrade_enabled``, ``upgrade_batch_size``,
            ``upgrade_cooldown_days``, ``upgrade_hourly_cap``,
            ``upgrade_sonarr_search_mode``, ``upgrade_lidarr_search_mode``,
            ``upgrade_readarr_search_mode``, ``upgrade_whisparr_search_mode``,
            ``upgrade_item_offset``, ``upgrade_series_offset``.

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
        "post_release_grace_hrs": "post_release_grace_hrs",
        "queue_limit": "queue_limit",
        "cutoff_enabled": "cutoff_enabled",
        "cutoff_batch_size": "cutoff_batch_size",
        "cutoff_cooldown_days": "cutoff_cooldown_days",
        "cutoff_hourly_cap": "cutoff_hourly_cap",
        "sonarr_search_mode": "sonarr_search_mode",
        "lidarr_search_mode": "lidarr_search_mode",
        "readarr_search_mode": "readarr_search_mode",
        "whisparr_search_mode": "whisparr_search_mode",
        "upgrade_enabled": "upgrade_enabled",
        "upgrade_batch_size": "upgrade_batch_size",
        "upgrade_cooldown_days": "upgrade_cooldown_days",
        "upgrade_hourly_cap": "upgrade_hourly_cap",
        "upgrade_sonarr_search_mode": "upgrade_sonarr_search_mode",
        "upgrade_lidarr_search_mode": "upgrade_lidarr_search_mode",
        "upgrade_readarr_search_mode": "upgrade_readarr_search_mode",
        "upgrade_whisparr_search_mode": "upgrade_whisparr_search_mode",
        "upgrade_item_offset": "upgrade_item_offset",
        "upgrade_series_offset": "upgrade_series_offset",
    }

    _search_mode_fields = {
        "sonarr_search_mode",
        "lidarr_search_mode",
        "readarr_search_mode",
        "whisparr_search_mode",
        "upgrade_sonarr_search_mode",
        "upgrade_lidarr_search_mode",
        "upgrade_readarr_search_mode",
        "upgrade_whisparr_search_mode",
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
        elif (field_name == "type" and isinstance(value, InstanceType)) or (
            field_name in _search_mode_fields and isinstance(value, StrEnum)
        ):
            value = value.value
        elif field_name in ("enabled", "cutoff_enabled", "upgrade_enabled"):
            value = int(bool(value))
        assignments.append(f"{col} = ?")
        values.append(value)

    if not assignments:
        # Nothing to do; return current state
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

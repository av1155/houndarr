"""Instances aggregate: SQL boundary for the ``instances`` table.

Track D.3 lands the read path: ``list_instances`` and
``get_instance`` plus the row mapper and the two fault-tolerant
column readers that keep tests with pre-v13 minimal rows compatible
with the current :class:`houndarr.services.instances.Instance`
dataclass.  Writes follow in D.4 (``insert_instance`` /
``update_instance`` / ``delete_instance`` with payload dataclasses).

The :class:`houndarr.services.instances.Instance` dataclass, the
search-mode :class:`enum.StrEnum` classes, and the value-mapping
invariants all stay in the service module through D.3; D.13 - D.20
reshape ``Instance`` into sub-struct facades and the row mapper will
follow that migration.  Until then the repository imports the
dataclass and enums from the service and the service's reads
delegate here via local imports to avoid an import-time cycle.

API keys are encrypted at rest: every row passes through
:func:`houndarr.crypto.decrypt` inside :func:`_row_to_instance`
before the :class:`Instance` leaves this boundary.  Callers that do
not have a master key cannot use this module; there is no
"raw row" public reader because no caller currently wants one.
"""

from __future__ import annotations

import aiosqlite

from houndarr.crypto import decrypt
from houndarr.database import get_db
from houndarr.services.instances import (
    Instance,
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SearchOrder,
    SonarrSearchMode,
    WhisparrSearchMode,
)


def _optional_row_int(row: aiosqlite.Row, key: str) -> int:
    """Return ``row[key]`` coerced to int, or ``0`` when the column is absent.

    Some tests seed the ``instances`` table with the pre-v13 column
    set (no ``monitored_total`` / ``unreleased_count`` /
    ``snapshot_refreshed_at``); this helper keeps those rows readable
    against the current dataclass without a migration.

    Args:
        row: aiosqlite row, typically from a ``SELECT *`` against
            the ``instances`` table.
        key: Column name to read.

    Returns:
        The column's value as an int, or ``0`` when the column or
        value is absent (``None``).
    """
    try:
        val = row[key]
    except (IndexError, KeyError):
        return 0
    return int(val) if val is not None else 0


def _optional_row_str(row: aiosqlite.Row, key: str) -> str:
    """Return ``row[key]`` coerced to str, or ``''`` when the column is absent.

    Args:
        row: aiosqlite row, typically from a ``SELECT *`` against
            the ``instances`` table.
        key: Column name to read.

    Returns:
        The column's value as a string, or ``''`` when the column
        or value is absent (``None``).
    """
    try:
        val = row[key]
    except (IndexError, KeyError):
        return ""
    return str(val) if val is not None else ""


def _row_to_instance(row: aiosqlite.Row, master_key: bytes) -> Instance:
    """Map an aiosqlite row to a decrypted :class:`Instance`.

    Decrypts ``encrypted_api_key`` with *master_key* and coerces each
    ``*_search_mode`` / ``search_order`` column through the matching
    :class:`enum.StrEnum`.  ``monitored_total`` / ``unreleased_count``
    / ``snapshot_refreshed_at`` route through the tolerant optional
    helpers so older test fixtures keep deserialising.

    Args:
        row: ``SELECT *`` row from the ``instances`` table.
        master_key: Fernet key used to decrypt ``encrypted_api_key``.

    Returns:
        A fully-populated :class:`Instance` with a plaintext
        ``api_key``.
    """
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
        missing_page_offset=row["missing_page_offset"],
        cutoff_page_offset=row["cutoff_page_offset"],
        allowed_time_window=row["allowed_time_window"],
        search_order=SearchOrder(row["search_order"]),
        monitored_total=_optional_row_int(row, "monitored_total"),
        unreleased_count=_optional_row_int(row, "unreleased_count"),
        snapshot_refreshed_at=_optional_row_str(row, "snapshot_refreshed_at"),
    )


async def get_instance(instance_id: int, *, master_key: bytes) -> Instance | None:
    """Fetch one instance row by primary key.

    Args:
        instance_id: Primary key of the row to read.
        master_key: Fernet key used to decrypt the stored API key.

    Returns:
        Decrypted :class:`Instance`, or ``None`` when no row exists
        for *instance_id*.
    """
    async with get_db() as db:
        async with db.execute("SELECT * FROM instances WHERE id = ?", (instance_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_instance(row, master_key)


async def list_instances(*, master_key: bytes) -> list[Instance]:
    """Return every instance row in stable id order.

    Args:
        master_key: Fernet key used to decrypt each stored API key.

    Returns:
        List of decrypted :class:`Instance` objects (may be empty);
        sort order is ``id ASC`` so the UI's row ordering matches
        insertion order.
    """
    async with get_db() as db:
        async with db.execute("SELECT * FROM instances ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [_row_to_instance(r, master_key) for r in rows]

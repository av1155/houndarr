"""Instances aggregate: SQL boundary for the ``instances`` table.

Track D.3 landed the read path (``list_instances`` / ``get_instance``
plus the row mapper and the fault-tolerant column readers that keep
tests with pre-v13 minimal rows compatible with the current
:class:`houndarr.services.instances.Instance` dataclass).  Track D.4
lands the write path: :class:`InstanceInsert` and
:class:`InstanceUpdate` payload dataclasses, ``insert_instance``,
``update_instance``, ``delete_instance``, and
``update_instance_snapshot``.

The :class:`~houndarr.services.instances.Instance` domain dataclass,
the search-mode :class:`enum.StrEnum` classes, and the value-mapping
invariants all stay in the service module through Track D's early
batches; D.13 - D.20 reshape ``Instance`` into sub-struct facades and
the row mapper will follow that migration.  Until then this
repository imports the dataclass and enums from the service and the
service's writes delegate here via local imports to avoid an
import-time cycle.

API keys are encrypted at rest: every write accepts plaintext, and
the repository runs :func:`houndarr.crypto.encrypt` before touching
SQL.  Every read decrypts via :func:`_row_to_instance`.  Payload
dataclasses therefore carry plaintext in their ``api_key`` field;
the encrypted blob never escapes this module.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any

import aiosqlite

from houndarr.config import (
    DEFAULT_ALLOWED_TIME_WINDOW,
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
    DEFAULT_SEARCH_ORDER,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_BATCH_SIZE,
    DEFAULT_UPGRADE_COOLDOWN_DAYS,
    DEFAULT_UPGRADE_HOURLY_CAP,
    DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE,
    DEFAULT_WHISPARR_V2_SEARCH_MODE,
)
from houndarr.crypto import decrypt, encrypt
from houndarr.database import get_db
from houndarr.services.instances import (
    Instance,
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SearchOrder,
    SonarrSearchMode,
    WhisparrV2SearchMode,
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
        whisparr_v2_search_mode=WhisparrV2SearchMode(row["whisparr_v2_search_mode"]),
        upgrade_enabled=bool(row["upgrade_enabled"]),
        upgrade_batch_size=row["upgrade_batch_size"],
        upgrade_cooldown_days=row["upgrade_cooldown_days"],
        upgrade_hourly_cap=row["upgrade_hourly_cap"],
        upgrade_sonarr_search_mode=SonarrSearchMode(row["upgrade_sonarr_search_mode"]),
        upgrade_lidarr_search_mode=LidarrSearchMode(row["upgrade_lidarr_search_mode"]),
        upgrade_readarr_search_mode=ReadarrSearchMode(row["upgrade_readarr_search_mode"]),
        upgrade_whisparr_v2_search_mode=WhisparrV2SearchMode(
            row["upgrade_whisparr_v2_search_mode"]
        ),
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


@dataclass(frozen=True, slots=True)
class InstanceInsert:
    """Payload for :func:`insert_instance`.

    Mirrors the columns written by the concrete SQL.  ``api_key`` is
    the plaintext value; :func:`insert_instance` encrypts it before
    the row lands.  Every field carries a default that matches the
    ``instances`` table DDL, so the three identifying fields
    (``name`` / ``type`` / ``url``) plus ``api_key`` are the only
    mandatory inputs for the common UI-driven create path.
    ``created_at`` and ``updated_at`` are not exposed here because
    SQLite fills them from the ``DEFAULT strftime(...)`` column spec.
    """

    name: str
    type: InstanceType
    url: str
    api_key: str
    enabled: bool = True
    batch_size: int = DEFAULT_BATCH_SIZE
    sleep_interval_mins: int = DEFAULT_SLEEP_INTERVAL_MINUTES
    hourly_cap: int = DEFAULT_HOURLY_CAP
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS
    post_release_grace_hrs: int = DEFAULT_POST_RELEASE_GRACE_HOURS
    queue_limit: int = DEFAULT_QUEUE_LIMIT
    cutoff_enabled: bool = False
    cutoff_batch_size: int = DEFAULT_CUTOFF_BATCH_SIZE
    cutoff_cooldown_days: int = DEFAULT_CUTOFF_COOLDOWN_DAYS
    cutoff_hourly_cap: int = DEFAULT_CUTOFF_HOURLY_CAP
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE)
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE)
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE)
    whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode(
        DEFAULT_WHISPARR_V2_SEARCH_MODE
    )
    upgrade_enabled: bool = False
    upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE
    upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS
    upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(
        DEFAULT_UPGRADE_SONARR_SEARCH_MODE
    )
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(
        DEFAULT_UPGRADE_LIDARR_SEARCH_MODE
    )
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(
        DEFAULT_UPGRADE_READARR_SEARCH_MODE
    )
    upgrade_whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode(
        DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE
    )
    allowed_time_window: str = DEFAULT_ALLOWED_TIME_WINDOW
    search_order: SearchOrder = SearchOrder(DEFAULT_SEARCH_ORDER)


@dataclass(frozen=True, slots=True)
class InstanceUpdate:
    """Payload for :func:`update_instance`.

    Every field is optional with ``None`` as the "do not touch"
    sentinel.  This is safe because every updatable column is
    non-nullable at the schema level, so no field's domain includes
    ``None`` legitimately.  ``api_key``, when supplied, is plaintext;
    :func:`update_instance` re-encrypts it.  The three snapshot
    columns and the four pagination-offset columns are included so
    the update surface matches the table schema exactly, even though
    the routine caller path uses :func:`update_instance_snapshot`
    and :func:`update_instance` for offsets respectively.
    """

    name: str | None = None
    type: InstanceType | None = None
    url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    batch_size: int | None = None
    sleep_interval_mins: int | None = None
    hourly_cap: int | None = None
    cooldown_days: int | None = None
    post_release_grace_hrs: int | None = None
    queue_limit: int | None = None
    cutoff_enabled: bool | None = None
    cutoff_batch_size: int | None = None
    cutoff_cooldown_days: int | None = None
    cutoff_hourly_cap: int | None = None
    sonarr_search_mode: SonarrSearchMode | None = None
    lidarr_search_mode: LidarrSearchMode | None = None
    readarr_search_mode: ReadarrSearchMode | None = None
    whisparr_v2_search_mode: WhisparrV2SearchMode | None = None
    upgrade_enabled: bool | None = None
    upgrade_batch_size: int | None = None
    upgrade_cooldown_days: int | None = None
    upgrade_hourly_cap: int | None = None
    upgrade_sonarr_search_mode: SonarrSearchMode | None = None
    upgrade_lidarr_search_mode: LidarrSearchMode | None = None
    upgrade_readarr_search_mode: ReadarrSearchMode | None = None
    upgrade_whisparr_v2_search_mode: WhisparrV2SearchMode | None = None
    upgrade_item_offset: int | None = None
    upgrade_series_offset: int | None = None
    missing_page_offset: int | None = None
    cutoff_page_offset: int | None = None
    allowed_time_window: str | None = None
    search_order: SearchOrder | None = None
    monitored_total: int | None = None
    unreleased_count: int | None = None
    snapshot_refreshed_at: str | None = None


# Columns whose value maps through ``StrEnum.value`` on the write path.
# Declared once so _sql_value_for stays table-driven instead of a
# long if / elif ladder that drifts as columns are added.
_ENUM_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "type",
        "sonarr_search_mode",
        "lidarr_search_mode",
        "readarr_search_mode",
        "whisparr_v2_search_mode",
        "upgrade_sonarr_search_mode",
        "upgrade_lidarr_search_mode",
        "upgrade_readarr_search_mode",
        "upgrade_whisparr_v2_search_mode",
        "search_order",
    }
)

# Columns stored as INTEGER that the dataclass exposes as bool.
_BOOL_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "enabled",
        "cutoff_enabled",
        "upgrade_enabled",
    }
)

# Columns whose write-side database column name differs from the
# dataclass field name.  Only ``api_key`` differs today; the rest
# map 1:1.
_FIELD_TO_COLUMN: dict[str, str] = {"api_key": "encrypted_api_key"}


def _sql_column_for(field_name: str) -> str:
    """Return the SQL column name for a payload field."""
    return _FIELD_TO_COLUMN.get(field_name, field_name)


def _sql_value_for(field_name: str, value: Any, master_key: bytes) -> Any:
    """Coerce a payload value to the form SQLite wants.

    Handles the three divergences between the Pythonic payload field
    type and the SQLite column type:

    - ``api_key`` is encrypted before storage.
    - ``StrEnum`` values flatten to their underlying string.
    - ``bool`` becomes ``int`` (SQLite stores 0 / 1 for BOOLEAN
      columns declared as INTEGER).
    """
    if field_name == "api_key":
        return encrypt(str(value), master_key)
    if field_name in _ENUM_UPDATE_FIELDS and isinstance(value, StrEnum):
        return value.value
    if field_name in _BOOL_UPDATE_FIELDS:
        return int(bool(value))
    return value


async def insert_instance(payload: InstanceInsert, *, master_key: bytes) -> int:
    """Insert a new instance row and return the assigned primary key.

    Args:
        payload: Fully-populated :class:`InstanceInsert` describing
            the new row.  ``api_key`` is plaintext; this function
            encrypts it with *master_key* before the INSERT.
        master_key: Fernet key used to encrypt ``api_key``.

    Returns:
        The ``id`` SQLite assigned to the new row.
    """
    encrypted = encrypt(payload.api_key, master_key)
    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO instances (
                name, type, url, encrypted_api_key,
                enabled, batch_size, sleep_interval_mins,
                hourly_cap, cooldown_days, post_release_grace_hrs, queue_limit,
                cutoff_enabled, cutoff_batch_size, cutoff_cooldown_days, cutoff_hourly_cap,
                sonarr_search_mode, lidarr_search_mode, readarr_search_mode,
                whisparr_v2_search_mode,
                upgrade_enabled, upgrade_batch_size, upgrade_cooldown_days,
                upgrade_hourly_cap,
                upgrade_sonarr_search_mode, upgrade_lidarr_search_mode,
                upgrade_readarr_search_mode, upgrade_whisparr_v2_search_mode,
                allowed_time_window, search_order
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                payload.name,
                payload.type.value,
                payload.url,
                encrypted,
                int(payload.enabled),
                payload.batch_size,
                payload.sleep_interval_mins,
                payload.hourly_cap,
                payload.cooldown_days,
                payload.post_release_grace_hrs,
                payload.queue_limit,
                int(payload.cutoff_enabled),
                payload.cutoff_batch_size,
                payload.cutoff_cooldown_days,
                payload.cutoff_hourly_cap,
                payload.sonarr_search_mode.value,
                payload.lidarr_search_mode.value,
                payload.readarr_search_mode.value,
                payload.whisparr_v2_search_mode.value,
                int(payload.upgrade_enabled),
                payload.upgrade_batch_size,
                payload.upgrade_cooldown_days,
                payload.upgrade_hourly_cap,
                payload.upgrade_sonarr_search_mode.value,
                payload.upgrade_lidarr_search_mode.value,
                payload.upgrade_readarr_search_mode.value,
                payload.upgrade_whisparr_v2_search_mode.value,
                payload.allowed_time_window,
                payload.search_order.value,
            ),
        )
        await db.commit()
        row_id = cur.lastrowid
        assert row_id is not None  # noqa: S101
        return row_id


async def update_instance(
    instance_id: int,
    payload: InstanceUpdate,
    *,
    master_key: bytes,
) -> None:
    """Partially update the instance row for *instance_id*.

    Every non-``None`` field in *payload* becomes a ``column = ?``
    assignment.  The SQL also bumps ``updated_at`` to ``now``
    whenever at least one field is supplied, mirroring the pre-D.4
    behaviour.  When every payload field is ``None`` this function is
    a no-op (no SQL is executed), so callers can pass a blank
    :class:`InstanceUpdate` cheaply.

    Args:
        instance_id: Primary key of the row to update.
        payload: :class:`InstanceUpdate` describing the partial
            update.  ``api_key``, when set, is plaintext.
        master_key: Fernet key used to re-encrypt ``api_key`` when
            it is part of the update.
    """
    assignments: list[str] = []
    values: list[Any] = []

    for field in fields(payload):
        value = getattr(payload, field.name)
        if value is None:
            continue
        assignments.append(f"{_sql_column_for(field.name)} = ?")
        values.append(_sql_value_for(field.name, value, master_key))

    if not assignments:
        return

    assignments.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    values.append(instance_id)
    sql = f"UPDATE instances SET {', '.join(assignments)} WHERE id = ?"  # noqa: S608  # nosec B608
    async with get_db() as db:
        await db.execute(sql, values)
        await db.commit()


async def delete_instance(instance_id: int) -> bool:
    """Delete the instance row for *instance_id*.

    Cooldown rows cascade via the FK declaration; ``search_log`` rows
    set their ``instance_id`` to NULL per ``ON DELETE SET NULL``.

    Args:
        instance_id: Primary key of the row to delete.

    Returns:
        ``True`` when a row was removed, ``False`` when no row
        matched the id.
    """
    async with get_db() as db:
        cur = await db.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
        await db.commit()
        return (cur.rowcount or 0) > 0


async def update_instance_snapshot(
    instance_id: int,
    *,
    monitored_total: int,
    unreleased_count: int,
) -> None:
    """Write the three snapshot columns for *instance_id*.

    Kept as a dedicated function rather than routed through
    :func:`update_instance` because the supervisor refreshes
    snapshots on a hot path and writing the literal ``strftime``
    for ``snapshot_refreshed_at`` is clearer as a one-shot SQL
    statement than as a payload transformation.  ``updated_at`` is
    refreshed alongside so downstream ``last_modified`` displays
    stay coherent.

    Args:
        instance_id: Primary key of the row to update.
        monitored_total: New value for ``monitored_total``.
        unreleased_count: New value for ``unreleased_count``.
    """
    async with get_db() as db:
        await db.execute(
            "UPDATE instances SET"
            " monitored_total = ?,"
            " unreleased_count = ?,"
            " snapshot_refreshed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (int(monitored_total), int(unreleased_count), instance_id),
        )
        await db.commit()

"""SQLite database connection, schema initialization, and migration helpers."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version: bump when adding new migrations
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 9

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
_INSTANCE_TYPES = "'radarr', 'sonarr', 'lidarr', 'readarr', 'whisparr'"
_ITEM_TYPES = "'episode', 'movie', 'album', 'book', 'whisparr_episode'"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    type                 TEXT    NOT NULL CHECK(type IN ({_INSTANCE_TYPES})),
    url                  TEXT    NOT NULL,
    encrypted_api_key    TEXT    NOT NULL DEFAULT '',
    batch_size           INTEGER NOT NULL DEFAULT 2,
    sleep_interval_mins  INTEGER NOT NULL DEFAULT 30,
    hourly_cap           INTEGER NOT NULL DEFAULT 4,
    cooldown_days        INTEGER NOT NULL DEFAULT 14,
    post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
    queue_limit            INTEGER NOT NULL DEFAULT 0,
    cutoff_enabled         INTEGER NOT NULL DEFAULT 0,
    cutoff_batch_size    INTEGER NOT NULL DEFAULT 1,
    cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
    cutoff_hourly_cap    INTEGER NOT NULL DEFAULT 1,
    sonarr_search_mode   TEXT    NOT NULL DEFAULT 'episode'
                                CHECK(sonarr_search_mode IN ('episode', 'season_context')),
    lidarr_search_mode   TEXT    NOT NULL DEFAULT 'album'
                                CHECK(lidarr_search_mode IN ('album', 'artist_context')),
    readarr_search_mode  TEXT    NOT NULL DEFAULT 'book'
                                CHECK(readarr_search_mode IN ('book', 'author_context')),
    whisparr_search_mode TEXT    NOT NULL DEFAULT 'episode'
                                CHECK(whisparr_search_mode IN ('episode', 'season_context')),
    upgrade_enabled      INTEGER NOT NULL DEFAULT 0,
    upgrade_batch_size   INTEGER NOT NULL DEFAULT 1,
    upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
    upgrade_hourly_cap   INTEGER NOT NULL DEFAULT 1,
    upgrade_sonarr_search_mode  TEXT NOT NULL DEFAULT 'episode'
                                CHECK(upgrade_sonarr_search_mode IN ('episode', 'season_context')),
    upgrade_lidarr_search_mode  TEXT NOT NULL DEFAULT 'album'
                                CHECK(upgrade_lidarr_search_mode IN ('album', 'artist_context')),
    upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book'
                                CHECK(upgrade_readarr_search_mode IN ('book', 'author_context')),
    upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode'
                                CHECK(upgrade_whisparr_search_mode
                                      IN ('episode', 'season_context')),
    upgrade_item_offset  INTEGER NOT NULL DEFAULT 0,
    upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
    missing_page_offset  INTEGER NOT NULL DEFAULT 1,
    cutoff_page_offset   INTEGER NOT NULL DEFAULT 1,
    enabled              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS cooldowns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL,
    item_type   TEXT    NOT NULL CHECK(item_type IN ({_ITEM_TYPES})),
    searched_at TEXT    NOT NULL,
    UNIQUE(instance_id, item_id, item_type)
);

CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup
    ON cooldowns(instance_id, item_type, searched_at);

CREATE TABLE IF NOT EXISTS search_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
    item_id     INTEGER,
    item_type   TEXT    CHECK(item_type IN ({_ITEM_TYPES})),
    search_kind TEXT,
    cycle_id    TEXT,
    cycle_trigger TEXT CHECK(cycle_trigger IN ('scheduled', 'run_now', 'system')),
    item_label  TEXT,
    action      TEXT    NOT NULL CHECK(action IN ('searched', 'skipped', 'error', 'info')),
    reason      TEXT,
    message     TEXT,
    timestamp   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_search_log_timestamp
    ON search_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_search_log_instance
    ON search_log(instance_id, timestamp DESC);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

_db_path: str = ""


def set_db_path(path: str) -> None:
    """Set the database path before the app starts."""
    global _db_path  # noqa: PLW0603
    _db_path = path


def get_db_path() -> str:
    return _db_path


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Yield a database connection with foreign keys enabled."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create tables and run migrations if needed."""
    async with get_db() as db:
        # WAL mode is a database-level setting that persists on the file.
        # Set it once here rather than on every connection.
        await db.execute("PRAGMA journal_mode=WAL")

        # Create all tables
        await db.executescript(_SCHEMA_SQL)

        # Check/set schema version
        async with db.execute("SELECT value FROM settings WHERE key = 'schema_version'") as cur:
            row = await cur.fetchone()

        if row is None:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            await _ensure_v3_indexes(db)
            await db.commit()
            logger.info("Database initialized at schema version %d", SCHEMA_VERSION)
        else:
            current = int(row["value"])
            if current < SCHEMA_VERSION:
                await _run_migrations(db, current)
            # Self-heal: re-apply the latest idempotent migration so that
            # a corrupted state (version bumped but columns missing) is
            # repaired automatically.  Each guard inside the migration
            # checks _column_exists first, so this is a no-op on a
            # healthy database.
            await _migrate_to_v9(db)
            await _ensure_v3_indexes(db)
            await db.commit()


async def _run_migrations(db: aiosqlite.Connection, from_version: int) -> None:
    """Apply incremental migrations from from_version to SCHEMA_VERSION."""
    if from_version < 2:
        await _migrate_to_v2(db)
    if from_version < 3:
        await _migrate_to_v3(db)
    if from_version < 4:
        await _migrate_to_v4(db)
    if from_version < 5:
        await _migrate_to_v5(db)
    if from_version < 6:
        await _migrate_to_v6(db)
    if from_version < 7:
        await _migrate_to_v7(db)
    if from_version < 8:
        await _migrate_to_v8(db)
    if from_version < 9:
        await _migrate_to_v9(db)

    logger.info("Migrated database from schema version %d to %d", from_version, SCHEMA_VERSION)
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'schema_version'",
        (str(SCHEMA_VERSION),),
    )
    await db.commit()


async def _migrate_to_v2(db: aiosqlite.Connection) -> None:
    """Add v2 columns for richer logs and cutoff-specific throttling."""
    if not await _column_exists(db, "search_log", "search_kind"):
        await db.execute("ALTER TABLE search_log ADD COLUMN search_kind TEXT")

    if not await _column_exists(db, "search_log", "item_label"):
        await db.execute("ALTER TABLE search_log ADD COLUMN item_label TEXT")

    if not await _column_exists(db, "instances", "cutoff_cooldown_days"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21"
        )

    if not await _column_exists(db, "instances", "cutoff_hourly_cap"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1"
        )


async def _migrate_to_v3(db: aiosqlite.Connection) -> None:
    """Add v3 columns for cycle and trigger log context."""
    if not await _column_exists(db, "search_log", "cycle_id"):
        await db.execute("ALTER TABLE search_log ADD COLUMN cycle_id TEXT")

    if not await _column_exists(db, "search_log", "cycle_trigger"):
        await db.execute("ALTER TABLE search_log ADD COLUMN cycle_trigger TEXT")


async def _migrate_to_v4(db: aiosqlite.Connection) -> None:
    """Add v4 column for Sonarr missing-search strategy mode."""
    if not await _column_exists(db, "instances", "sonarr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN sonarr_search_mode TEXT NOT NULL DEFAULT 'episode'"
        )


async def _migrate_to_v5(db: aiosqlite.Connection) -> None:
    """Expand CHECK constraints for multi-app support and add per-app search mode columns.

    SQLite cannot ALTER CHECK constraints, so affected tables are recreated.
    Foreign keys are temporarily disabled to prevent CASCADE deletes when
    the parent ``instances`` table is dropped and recreated.
    New columns (lidarr/readarr/whisparr search modes) are added afterwards
    via ALTER TABLE since they have defaults.
    """
    # Disable FK enforcement during table recreation to prevent CASCADE deletes
    await db.execute("PRAGMA foreign_keys=OFF")

    # -- 1) Recreate instances with expanded type CHECK ----------------------
    await db.execute(
        f"""
        CREATE TABLE instances_new (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT    NOT NULL,
            type                 TEXT    NOT NULL CHECK(type IN ({_INSTANCE_TYPES})),
            url                  TEXT    NOT NULL,
            encrypted_api_key    TEXT    NOT NULL DEFAULT '',
            batch_size           INTEGER NOT NULL DEFAULT 2,
            sleep_interval_mins  INTEGER NOT NULL DEFAULT 30,
            hourly_cap           INTEGER NOT NULL DEFAULT 4,
            cooldown_days        INTEGER NOT NULL DEFAULT 14,
            unreleased_delay_hrs INTEGER NOT NULL DEFAULT 36,
            cutoff_enabled       INTEGER NOT NULL DEFAULT 0,
            cutoff_batch_size    INTEGER NOT NULL DEFAULT 1,
            cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
            cutoff_hourly_cap    INTEGER NOT NULL DEFAULT 1,
            sonarr_search_mode   TEXT    NOT NULL DEFAULT 'episode'
                                        CHECK(sonarr_search_mode IN ('episode', 'season_context')),
            enabled              INTEGER NOT NULL DEFAULT 1,
            created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    await db.execute(
        """
        INSERT INTO instances_new (
            id, name, type, url, encrypted_api_key,
            batch_size, sleep_interval_mins, hourly_cap, cooldown_days,
            unreleased_delay_hrs, cutoff_enabled, cutoff_batch_size,
            cutoff_cooldown_days, cutoff_hourly_cap, sonarr_search_mode,
            enabled, created_at, updated_at
        )
        SELECT
            id, name, type, url, encrypted_api_key,
            batch_size, sleep_interval_mins, hourly_cap, cooldown_days,
            unreleased_delay_hrs, cutoff_enabled, cutoff_batch_size,
            cutoff_cooldown_days, cutoff_hourly_cap, sonarr_search_mode,
            enabled, created_at, updated_at
        FROM instances
        """
    )
    await db.execute("DROP TABLE instances")
    await db.execute("ALTER TABLE instances_new RENAME TO instances")

    # -- 2) Recreate cooldowns with expanded item_type CHECK -----------------
    await db.execute(
        f"""
        CREATE TABLE cooldowns_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
            item_id     INTEGER NOT NULL,
            item_type   TEXT    NOT NULL CHECK(item_type IN ({_ITEM_TYPES})),
            searched_at TEXT    NOT NULL,
            UNIQUE(instance_id, item_id, item_type)
        )
        """
    )
    await db.execute(
        """
        INSERT INTO cooldowns_new (id, instance_id, item_id, item_type, searched_at)
        SELECT id, instance_id, item_id, item_type, searched_at
        FROM cooldowns
        """
    )
    await db.execute("DROP TABLE cooldowns")
    await db.execute("ALTER TABLE cooldowns_new RENAME TO cooldowns")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup "
        "ON cooldowns(instance_id, item_type, searched_at)"
    )

    # -- 3) Recreate search_log with expanded item_type CHECK ----------------
    await db.execute(
        f"""
        CREATE TABLE search_log_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
            item_id     INTEGER,
            item_type   TEXT    CHECK(item_type IN ({_ITEM_TYPES})),
            search_kind TEXT,
            cycle_id    TEXT,
            cycle_trigger TEXT CHECK(cycle_trigger IN ('scheduled', 'run_now', 'system')),
            item_label  TEXT,
            action      TEXT    NOT NULL CHECK(action IN ('searched', 'skipped', 'error', 'info')),
            reason      TEXT,
            message     TEXT,
            timestamp   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    await db.execute(
        """
        INSERT INTO search_log_new (
            id, instance_id, item_id, item_type, search_kind,
            cycle_id, cycle_trigger, item_label, action, reason, message, timestamp
        )
        SELECT
            id, instance_id, item_id, item_type, search_kind,
            cycle_id, cycle_trigger, item_label, action, reason, message, timestamp
        FROM search_log
        """
    )
    await db.execute("DROP TABLE search_log")
    await db.execute("ALTER TABLE search_log_new RENAME TO search_log")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_timestamp ON search_log(timestamp DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_instance "
        "ON search_log(instance_id, timestamp DESC)"
    )

    # -- 4) Add new per-app search mode columns via ALTER TABLE --------------
    if not await _column_exists(db, "instances", "lidarr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN lidarr_search_mode TEXT NOT NULL DEFAULT 'album'"
        )
    if not await _column_exists(db, "instances", "readarr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN readarr_search_mode TEXT NOT NULL DEFAULT 'book'"
        )
    if not await _column_exists(db, "instances", "whisparr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN whisparr_search_mode TEXT NOT NULL DEFAULT 'episode'"
        )

    # Re-enable FK enforcement after table recreation
    await db.execute("PRAGMA foreign_keys=ON")


async def _migrate_to_v6(db: aiosqlite.Connection) -> None:
    """Rename ``unreleased_delay_hrs`` to ``post_release_grace_hrs``.

    The old 36-hour default is migrated to the new 6-hour default for
    instances that were never customised.  Instances with a user-set value
    (anything other than 36) keep their existing value.

    SQLite does not support ``RENAME COLUMN`` with default changes, so the
    migration adds the new column, copies values, and drops the old column
    via table recreation.
    """
    if not await _column_exists(db, "instances", "unreleased_delay_hrs"):
        # Already migrated (e.g. fresh DB created at schema version 6).
        return

    # Add the new column
    await db.execute(
        "ALTER TABLE instances ADD COLUMN post_release_grace_hrs INTEGER NOT NULL DEFAULT 6"
    )

    # Copy existing values: 36 → 6 (old default → new default), others as-is
    await db.execute(
        """
        UPDATE instances
        SET post_release_grace_hrs = CASE
            WHEN unreleased_delay_hrs = 36 THEN 6
            ELSE unreleased_delay_hrs
        END
        """
    )

    # Drop the old column.  SQLite 3.35+ supports DROP COLUMN directly.
    # For older SQLite (pre-3.35), table recreation is needed, but Python
    # 3.12 ships with SQLite ≥3.40, so DROP COLUMN is safe.
    await db.execute("ALTER TABLE instances DROP COLUMN unreleased_delay_hrs")


async def _migrate_to_v7(db: aiosqlite.Connection) -> None:
    """Add ``queue_limit`` column for download-queue backpressure.

    Default is 0 (disabled; no backpressure check).  When set to a positive
    value, the search loop skips cycles while the *arr download queue exceeds
    the configured threshold.
    """
    if not await _column_exists(db, "instances", "queue_limit"):
        await db.execute("ALTER TABLE instances ADD COLUMN queue_limit INTEGER NOT NULL DEFAULT 0")


async def _migrate_to_v8(db: aiosqlite.Connection) -> None:
    """Add upgrade search columns for the opt-in third search pass.

    Ten new columns on ``instances``: four rate controls, four per-app search
    modes (dedicated to the upgrade pass), and two offset-tracking columns.
    All have NOT NULL DEFAULT so the ALTER TABLE is safe for existing rows.
    """
    # Rate controls
    if not await _column_exists(db, "instances", "upgrade_enabled"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_enabled INTEGER NOT NULL DEFAULT 0"
        )
    if not await _column_exists(db, "instances", "upgrade_batch_size"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_batch_size INTEGER NOT NULL DEFAULT 1"
        )
    if not await _column_exists(db, "instances", "upgrade_cooldown_days"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90"
        )
    if not await _column_exists(db, "instances", "upgrade_hourly_cap"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1"
        )

    # Per-app search modes (dedicated to upgrade pass)
    if not await _column_exists(db, "instances", "upgrade_sonarr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_sonarr_search_mode"
            " TEXT NOT NULL DEFAULT 'episode'"
        )
    if not await _column_exists(db, "instances", "upgrade_lidarr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_lidarr_search_mode"
            " TEXT NOT NULL DEFAULT 'album'"
        )
    if not await _column_exists(db, "instances", "upgrade_readarr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_readarr_search_mode"
            " TEXT NOT NULL DEFAULT 'book'"
        )
    if not await _column_exists(db, "instances", "upgrade_whisparr_search_mode"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_whisparr_search_mode"
            " TEXT NOT NULL DEFAULT 'episode'"
        )

    # Offset tracking (operational state, not user-configurable)
    if not await _column_exists(db, "instances", "upgrade_item_offset"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_item_offset INTEGER NOT NULL DEFAULT 0"
        )
    if not await _column_exists(db, "instances", "upgrade_series_offset"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_series_offset INTEGER NOT NULL DEFAULT 0"
        )


async def _migrate_to_v9(db: aiosqlite.Connection) -> None:
    """Add page-offset tracking for missing and cutoff passes."""
    if not await _column_exists(db, "instances", "missing_page_offset"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN missing_page_offset INTEGER NOT NULL DEFAULT 1"
        )
    if not await _column_exists(db, "instances", "cutoff_page_offset"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN cutoff_page_offset INTEGER NOT NULL DEFAULT 1"
        )


async def _ensure_v3_indexes(db: aiosqlite.Connection) -> None:
    """Create v3 indexes that depend on post-v2 columns."""
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_cycle ON search_log(cycle_id, timestamp DESC)"
    )


async def _column_exists(db: aiosqlite.Connection, table_name: str, column_name: str) -> bool:
    """Return whether *column_name* exists on *table_name*."""
    async with db.execute(f"PRAGMA table_info({table_name})") as cur:  # noqa: S608  # nosec B608
        rows = await cur.fetchall()
    return any(row[1] == column_name for row in rows)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


async def get_setting(key: str, default: str | None = None) -> str | None:
    """Fetch a single setting value by key."""
    async with get_db() as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return str(row["value"]) if row else default


async def set_setting(key: str, value: str) -> None:
    """Upsert a setting."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Log retention
# ---------------------------------------------------------------------------


async def purge_old_logs(retention_days: int) -> int:
    """Delete ``search_log`` rows older than *retention_days* days.

    Called at startup (and optionally on a schedule) to prevent unbounded
    log growth on long-running instances.

    Args:
        retention_days: Rows with a ``timestamp`` older than this many days
            are deleted.  Pass ``0`` or a negative value to disable purging.

    Returns:
        Number of rows deleted (0 if retention is disabled or nothing to purge).
    """
    if retention_days <= 0:
        return 0

    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM search_log WHERE timestamp < datetime('now', ? || ' days')",
            (f"-{retention_days}",),
        )
        await db.commit()
        return cur.rowcount or 0

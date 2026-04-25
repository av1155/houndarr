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
SCHEMA_VERSION = 17

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
_INSTANCE_TYPES = "'radarr', 'sonarr', 'lidarr', 'readarr', 'whisparr_v2', 'whisparr_v3'"
_ITEM_TYPES = "'episode', 'movie', 'album', 'book', 'whisparr_v2_episode', 'whisparr_v3_movie'"

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
    whisparr_v2_search_mode TEXT NOT NULL DEFAULT 'episode'
                                CHECK(whisparr_v2_search_mode IN ('episode', 'season_context')),
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
    upgrade_whisparr_v2_search_mode TEXT NOT NULL DEFAULT 'episode'
                                CHECK(upgrade_whisparr_v2_search_mode
                                      IN ('episode', 'season_context')),
    upgrade_item_offset  INTEGER NOT NULL DEFAULT 0,
    upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
    upgrade_series_window_size INTEGER NOT NULL DEFAULT 5
                                    CHECK(upgrade_series_window_size >= 1
                                          AND upgrade_series_window_size <= 100),
    missing_page_offset  INTEGER NOT NULL DEFAULT 1,
    cutoff_page_offset   INTEGER NOT NULL DEFAULT 1,
    allowed_time_window  TEXT    NOT NULL DEFAULT '',
    search_order         TEXT    NOT NULL DEFAULT 'random'
                                CHECK(search_order IN ('chronological', 'random')),
    monitored_total      INTEGER NOT NULL DEFAULT 0,
    unreleased_count     INTEGER NOT NULL DEFAULT 0,
    snapshot_refreshed_at TEXT   NOT NULL DEFAULT '',
    enabled              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS cooldowns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL,
    item_type   TEXT    NOT NULL CHECK(item_type IN ({_ITEM_TYPES})),
    search_kind TEXT    NOT NULL DEFAULT 'missing'
                        CHECK(search_kind IN ('missing', 'cutoff', 'upgrade')),
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
            await _migrate_to_v10(db)
            await _migrate_to_v11(db)
            await _migrate_to_v12(db)
            await _migrate_to_v13(db)
            await _migrate_to_v14(db)
            await _migrate_to_v15(db)
            await _migrate_to_v16(db)
            await _migrate_to_v17(db)
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
    if from_version < 10:
        await _migrate_to_v10(db)
    if from_version < 11:
        await _migrate_to_v11(db)
    if from_version < 12:
        await _migrate_to_v12(db)
    if from_version < 13:
        await _migrate_to_v13(db)
    if from_version < 14:
        await _migrate_to_v14(db)
    if from_version < 15:
        await _migrate_to_v15(db)
    if from_version < 16:
        await _migrate_to_v16(db)
    if from_version < 17:
        await _migrate_to_v17(db)

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
        SELECT id, instance_id, item_id,
               CASE WHEN item_type = 'whisparr_episode' THEN 'whisparr_v2_episode'
                    ELSE item_type END,
               searched_at
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
            id, instance_id, item_id,
            CASE WHEN item_type = 'whisparr_episode' THEN 'whisparr_v2_episode'
                 ELSE item_type END,
            search_kind, cycle_id, cycle_trigger, item_label, action, reason, message, timestamp
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


async def _migrate_to_v10(db: aiosqlite.Connection) -> None:
    """Rename ``whisparr`` instance type to ``whisparr_v2`` and expand CHECK constraints.

    Whisparr v2 (Sonarr-based) and v3 (Radarr-based) are separate applications.
    Existing ``whisparr`` instances become ``whisparr_v2``; the new
    ``whisparr_v3`` type is added alongside it.

    SQLite cannot ALTER CHECK constraints, so affected tables are recreated.
    On a healthy database where the CHECK already includes ``whisparr_v3``,
    this is a no-op (detected via the DDL stored in ``sqlite_master``).

    Self-heal note: when this runs after :func:`_migrate_to_v16` (which
    happens during the self-heal pass on a corrupted v10 database), the
    ``whisparr_search_mode`` column has already been renamed to
    ``whisparr_v2_search_mode``.  The two column-name branches below
    select whichever name currently exists so the rebuild succeeds in
    both orders.
    """
    # Guard: skip the expensive table recreation if the migration was already
    # applied.  Querying sqlite_master for the CREATE TABLE DDL is the
    # cheapest reliable way to detect whether the CHECK constraint has been
    # expanded, because SQLite stores the original DDL verbatim.
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='instances'"
    ) as cur:
        row = await cur.fetchone()
    if row and "whisparr_v3" in (row[0] or ""):
        return

    # Detect whether v16 has already renamed the columns.  In incremental
    # migrations from a real v10 database this is False and the rebuild uses
    # the original column names.  In the self-heal path on a corrupted v10
    # database, _run_migrations applies v11..v16 first, the column rename
    # in v16 has run, and the rebuild must use the new names.
    v16_already_applied = await _column_exists(db, "instances", "whisparr_v2_search_mode")
    new_col = "whisparr_v2_search_mode" if v16_already_applied else "whisparr_search_mode"
    new_upgrade_col = (
        "upgrade_whisparr_v2_search_mode" if v16_already_applied else "upgrade_whisparr_search_mode"
    )

    await db.execute("PRAGMA foreign_keys=OFF")

    # Guard: clean up any leftover temp tables from a partial previous run.
    await db.execute("DROP TABLE IF EXISTS instances_new")
    await db.execute("DROP TABLE IF EXISTS cooldowns_new")
    await db.execute("DROP TABLE IF EXISTS search_log_new")

    # -- 1) Recreate instances with expanded type CHECK + rename whisparr ------
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
            {new_col} TEXT    NOT NULL DEFAULT 'episode'
                                 CHECK({new_col}
                                       IN ('episode', 'season_context')),
            upgrade_enabled      INTEGER NOT NULL DEFAULT 0,
            upgrade_batch_size   INTEGER NOT NULL DEFAULT 1,
            upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
            upgrade_hourly_cap   INTEGER NOT NULL DEFAULT 1,
            upgrade_sonarr_search_mode  TEXT NOT NULL DEFAULT 'episode'
                                        CHECK(upgrade_sonarr_search_mode
                                              IN ('episode', 'season_context')),
            upgrade_lidarr_search_mode  TEXT NOT NULL DEFAULT 'album'
                                        CHECK(upgrade_lidarr_search_mode
                                              IN ('album', 'artist_context')),
            upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book'
                                        CHECK(upgrade_readarr_search_mode
                                              IN ('book', 'author_context')),
            {new_upgrade_col} TEXT NOT NULL DEFAULT 'episode'
                                        CHECK({new_upgrade_col}
                                              IN ('episode', 'season_context')),
            upgrade_item_offset  INTEGER NOT NULL DEFAULT 0,
            upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
            missing_page_offset  INTEGER NOT NULL DEFAULT 1,
            cutoff_page_offset   INTEGER NOT NULL DEFAULT 1,
            enabled              INTEGER NOT NULL DEFAULT 1,
            created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """  # noqa: S608  # nosec B608
    )
    await db.execute(
        f"""
        INSERT INTO instances_new
        SELECT id, name,
               CASE WHEN type='whisparr' THEN 'whisparr_v2' ELSE type END,
               url, encrypted_api_key,
               batch_size, sleep_interval_mins, hourly_cap, cooldown_days,
               post_release_grace_hrs, queue_limit, cutoff_enabled,
               cutoff_batch_size, cutoff_cooldown_days, cutoff_hourly_cap,
               sonarr_search_mode, lidarr_search_mode, readarr_search_mode,
               {new_col},
               upgrade_enabled, upgrade_batch_size, upgrade_cooldown_days,
               upgrade_hourly_cap,
               upgrade_sonarr_search_mode, upgrade_lidarr_search_mode,
               upgrade_readarr_search_mode, {new_upgrade_col},
               upgrade_item_offset, upgrade_series_offset,
               missing_page_offset, cutoff_page_offset,
               enabled, created_at, updated_at
        FROM instances
        """  # noqa: S608  # nosec B608
    )
    await db.execute("DROP TABLE instances")
    await db.execute("ALTER TABLE instances_new RENAME TO instances")

    # -- 2) Recreate cooldowns with expanded item_type CHECK -------------------
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
        SELECT id, instance_id, item_id,
               CASE WHEN item_type = 'whisparr_episode' THEN 'whisparr_v2_episode'
                    ELSE item_type END,
               searched_at
        FROM cooldowns
        """
    )
    await db.execute("DROP TABLE cooldowns")
    await db.execute("ALTER TABLE cooldowns_new RENAME TO cooldowns")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup"
        " ON cooldowns(instance_id, item_type, searched_at)"
    )

    # -- 3) Recreate search_log with expanded item_type CHECK ------------------
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
            cycle_id, cycle_trigger, item_label, action,
            reason, message, timestamp
        )
        SELECT
            id, instance_id, item_id,
            CASE WHEN item_type = 'whisparr_episode' THEN 'whisparr_v2_episode'
                 ELSE item_type END,
            search_kind, cycle_id, cycle_trigger, item_label, action,
            reason, message, timestamp
        FROM search_log
        """
    )
    await db.execute("DROP TABLE search_log")
    await db.execute("ALTER TABLE search_log_new RENAME TO search_log")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_timestamp ON search_log(timestamp DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_instance"
        " ON search_log(instance_id, timestamp DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_cycle ON search_log(cycle_id, timestamp DESC)"
    )

    await db.execute("PRAGMA foreign_keys=ON")


async def _migrate_to_v11(db: aiosqlite.Connection) -> None:
    """Add ``allowed_time_window`` column for per-instance search schedules.

    Default is the empty string (always allowed; no gate).  When set to a
    non-empty spec like ``"09:00-23:00"`` or ``"09:00-12:00,18:00-22:00"``,
    the search loop skips scheduled cycles that fall outside the window.
    """
    if not await _column_exists(db, "instances", "allowed_time_window"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN allowed_time_window TEXT NOT NULL DEFAULT ''"
        )


async def _migrate_to_v12(db: aiosqlite.Connection) -> None:
    """Add ``search_order`` column for per-instance random search ordering.

    Fresh installs default to ``'random'`` (see ``_SCHEMA_SQL``); the
    migration keeps existing rows on ``'chronological'`` so upgrades do not
    silently change behaviour for instances that have been running for
    months.  New instances added via the UI always get the ``config.py``
    default (currently ``'random'``).  The CHECK constraint lives in
    ``_SCHEMA_SQL``; SQLite's ``ALTER TABLE ADD COLUMN`` cannot add a CHECK,
    so migrated rows rely on the service layer (which only accepts
    ``SearchOrder`` enum values) for validation.
    """
    if not await _column_exists(db, "instances", "search_order"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN search_order TEXT NOT NULL DEFAULT 'chronological'"
        )


async def _migrate_to_v13(db: aiosqlite.Connection) -> None:
    """Add per-instance snapshot columns used by the redesigned dashboard.

    ``monitored_total`` and ``unreleased_count`` are populated by the
    supervisor's ``refresh_instance_snapshots`` task via each arr's
    ``/wanted/*?pageSize=1`` probes (or Whisparr v3's cached ``/movie``
    filter).  ``snapshot_refreshed_at`` records when the values were
    last written so stale snapshots are visible on the Settings page.
    """
    if not await _column_exists(db, "instances", "monitored_total"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN monitored_total INTEGER NOT NULL DEFAULT 0"
        )
    if not await _column_exists(db, "instances", "unreleased_count"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN unreleased_count INTEGER NOT NULL DEFAULT 0"
        )
    if not await _column_exists(db, "instances", "snapshot_refreshed_at"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN snapshot_refreshed_at TEXT NOT NULL DEFAULT ''"
        )


async def _migrate_to_v14(db: aiosqlite.Connection) -> None:
    """Stamp ``search_kind`` on cooldown rows at insert time.

    Previously ``cooldown_breakdown`` was derived at read time by joining
    every cooldown row to its most-recent ``search_log`` row and reading
    ``search_kind`` off that.  Two fragilities fall out of this: the
    classification flips whenever a later search kind is logged for the
    same item, and it quietly defaults to ``"missing"`` if the log row
    is absent (e.g. retention pruned it, or a migration truncated the
    table).  Also a correlated subquery per cooldown row per
    ``/api/status`` poll.

    Stamp the kind on the row itself at INSERT time so the cooldowns
    table carries its own classification.  Backfill existing rows from
    the newest ``searched`` log entry per item, defaulting to
    ``"missing"`` when no log match exists (preserving current read
    behaviour for pre-migration data).  A nullable-then-default dance
    is unnecessary because the column has a ``NOT NULL DEFAULT
    'missing'`` literal: rows added before backfill simply carry the
    default, which matches what the old read-path returned anyway.

    The ``(instance_id, item_id, item_type)`` UNIQUE constraint stays
    as-is: cooldowns are about pacing indexer requests per item, not
    per pass, so one row per item-per-instance is correct.  The
    stamped ``search_kind`` is the kind of the MOST RECENT search that
    updated that row, which is what the reconciliation path matches
    against the *arr's per-pass wanted sets.
    """
    if not await _column_exists(db, "cooldowns", "search_kind"):
        await db.execute(
            "ALTER TABLE cooldowns ADD COLUMN search_kind TEXT NOT NULL DEFAULT 'missing'"
        )
    # Backfill any rows that still carry the literal default from the
    # ALTER.  The SELECT mirrors the classifier that used to live in
    # services/metrics.py's cooldown breakdown SQL; newer log rows
    # override older ones so the stamp matches what the dashboard used
    # to infer at read time.
    await db.execute(
        """
        UPDATE cooldowns
           SET search_kind = (
                 SELECT sl.search_kind
                   FROM search_log sl
                  WHERE sl.instance_id = cooldowns.instance_id
                    AND sl.item_id     = cooldowns.item_id
                    AND sl.item_type   = cooldowns.item_type
                    AND sl.action      = 'searched'
                    AND sl.search_kind IN ('missing', 'cutoff', 'upgrade')
                  ORDER BY sl.timestamp DESC
                  LIMIT 1
               )
         WHERE EXISTS (
                 SELECT 1 FROM search_log sl2
                  WHERE sl2.instance_id = cooldowns.instance_id
                    AND sl2.item_id     = cooldowns.item_id
                    AND sl2.item_type   = cooldowns.item_type
                    AND sl2.action      = 'searched'
                    AND sl2.search_kind IN ('missing', 'cutoff', 'upgrade')
               )
        """
    )
    # Index creation lives in _ensure_v3_indexes so fresh installs
    # (which skip migrations) still get it; the helper runs after both
    # the fresh-install DDL and the upgrade migration path.


async def _migrate_to_v15(db: aiosqlite.Connection) -> None:
    """Enforce the ``search_kind`` CHECK constraint on migrated databases.

    v14's ``ALTER TABLE ADD COLUMN`` could only set the ``NOT NULL
    DEFAULT 'missing'`` clause; SQLite does not let you append a CHECK
    constraint to an existing column.  Fresh installs already carry
    the ``CHECK(search_kind IN ('missing', 'cutoff', 'upgrade'))``
    clause from ``_SCHEMA_SQL``, but databases that migrated through
    v14 silently accept any string in that column.  Re-create the
    table with the full CHECK clause and copy the rows across so
    every install enforces the same invariant.

    The rebuild is idempotent: the sentinel inspects
    ``sqlite_master.sql`` for the ``CHECK(search_kind IN`` token and
    returns immediately when the table already carries it.  Any row
    whose stamped kind falls outside the three allowed values is
    coerced to ``'missing'`` during the copy so the new CHECK does
    not reject pre-existing data; such rows should not exist in
    practice (the app writes only valid kinds) but defence-in-depth
    keeps the migration from failing on a corrupted snapshot.
    """
    if await _cooldowns_has_search_kind_check(db):
        return

    await db.execute("PRAGMA foreign_keys=OFF")
    try:
        await db.execute(
            f"""
            CREATE TABLE cooldowns_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id     INTEGER NOT NULL,
                item_type   TEXT    NOT NULL CHECK(item_type IN ({_ITEM_TYPES})),
                search_kind TEXT    NOT NULL DEFAULT 'missing'
                                    CHECK(search_kind IN ('missing', 'cutoff', 'upgrade')),
                searched_at TEXT    NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO cooldowns_new
                (id, instance_id, item_id, item_type, search_kind, searched_at)
            SELECT id,
                   instance_id,
                   item_id,
                   item_type,
                   CASE
                       WHEN search_kind IN ('missing', 'cutoff', 'upgrade')
                           THEN search_kind
                       ELSE 'missing'
                   END AS search_kind,
                   searched_at
              FROM cooldowns
            """
        )
        await db.execute("DROP TABLE cooldowns")
        await db.execute("ALTER TABLE cooldowns_new RENAME TO cooldowns")
        # Recreate every index that referenced the old table.  The
        # search_kind index is also emitted by _ensure_v3_indexes, but
        # the lookup index lived only in _SCHEMA_SQL so without this
        # line migrated databases would lose it.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup "
            "ON cooldowns(instance_id, item_type, searched_at)"
        )
    finally:
        await db.execute("PRAGMA foreign_keys=ON")


async def _migrate_to_v16(db: aiosqlite.Connection) -> None:
    """Disambiguate Whisparr v2 columns and item_type values from the family name.

    Renames the two ``instances`` columns ``whisparr_search_mode`` and
    ``upgrade_whisparr_search_mode`` to ``whisparr_v2_search_mode`` and
    ``upgrade_whisparr_v2_search_mode`` so the column names match the
    ``InstanceType.whisparr_v2`` row those modes apply to.  Renames the
    ``cooldowns`` and ``search_log`` ``item_type`` value
    ``'whisparr_episode'`` to ``'whisparr_v2_episode'`` for the same reason.

    The column renames use ``ALTER TABLE ... RENAME COLUMN`` (SQLite 3.25+,
    Python 3.12 ships ≥3.40 so this is safe) and skip when the new column
    name is already present so the migration is idempotent.

    Updating the ``item_type`` value requires a table rebuild because
    the existing CHECK constraint allows ``'whisparr_episode'`` but not
    ``'whisparr_v2_episode'``.  The rebuild mirrors the v15 ``cooldowns``
    rebuild and the v10 ``search_log`` rebuild: drop into the temp
    ``*_new`` table with the v16 CHECK clause, copy rows while
    rewriting the value, then ``DROP`` and ``RENAME``.  Idempotent via
    a sentinel that inspects ``sqlite_master.sql`` for the ``v2_episode``
    token.
    """
    # Column renames on instances (skip when already migrated).
    if not await _column_exists(db, "instances", "whisparr_v2_search_mode"):
        await db.execute(
            "ALTER TABLE instances RENAME COLUMN whisparr_search_mode TO whisparr_v2_search_mode"
        )
    if not await _column_exists(db, "instances", "upgrade_whisparr_v2_search_mode"):
        await db.execute(
            "ALTER TABLE instances RENAME COLUMN upgrade_whisparr_search_mode"
            " TO upgrade_whisparr_v2_search_mode"
        )

    # Table rebuilds for cooldowns + search_log if the v16 item_type is not
    # yet allowed by their CHECK clauses.
    if await _cooldowns_has_v2_item_type_check(db):
        return

    await db.execute("PRAGMA foreign_keys=OFF")
    try:
        # Clean up any leftover temp tables from a partial previous run.
        await db.execute("DROP TABLE IF EXISTS cooldowns_new")
        await db.execute("DROP TABLE IF EXISTS search_log_new")

        # cooldowns rebuild with the v16 item_type CHECK + UPDATE rows.
        await db.execute(
            f"""
            CREATE TABLE cooldowns_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id     INTEGER NOT NULL,
                item_type   TEXT    NOT NULL CHECK(item_type IN ({_ITEM_TYPES})),
                search_kind TEXT    NOT NULL DEFAULT 'missing'
                                    CHECK(search_kind IN ('missing', 'cutoff', 'upgrade')),
                searched_at TEXT    NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO cooldowns_new
                (id, instance_id, item_id, item_type, search_kind, searched_at)
            SELECT id,
                   instance_id,
                   item_id,
                   CASE
                       WHEN item_type = 'whisparr_episode' THEN 'whisparr_v2_episode'
                       ELSE item_type
                   END AS item_type,
                   search_kind,
                   searched_at
              FROM cooldowns
            """
        )
        await db.execute("DROP TABLE cooldowns")
        await db.execute("ALTER TABLE cooldowns_new RENAME TO cooldowns")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup "
            "ON cooldowns(instance_id, item_type, searched_at)"
        )

        # search_log rebuild with the v16 item_type CHECK + UPDATE rows.
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
                action      TEXT    NOT NULL
                                    CHECK(action IN ('searched', 'skipped', 'error', 'info')),
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
                cycle_id, cycle_trigger, item_label, action,
                reason, message, timestamp
            )
            SELECT
                id, instance_id, item_id,
                CASE
                    WHEN item_type = 'whisparr_episode' THEN 'whisparr_v2_episode'
                    ELSE item_type
                END AS item_type,
                search_kind, cycle_id, cycle_trigger, item_label, action,
                reason, message, timestamp
            FROM search_log
            """
        )
        await db.execute("DROP TABLE search_log")
        await db.execute("ALTER TABLE search_log_new RENAME TO search_log")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_log_timestamp ON search_log(timestamp DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_log_instance"
            " ON search_log(instance_id, timestamp DESC)"
        )
    finally:
        await db.execute("PRAGMA foreign_keys=ON")


async def _cooldowns_has_v2_item_type_check(db: aiosqlite.Connection) -> bool:
    """Return whether ``cooldowns`` already lists ``whisparr_v2_episode`` in CHECK.

    Used by :func:`_migrate_to_v16` to skip the rebuild on a database that
    has already migrated.  The fresh-install DDL emits the
    ``whisparr_v2_episode`` token directly; rebuilt tables carry the same
    literal.  Whitespace varies between SQLite versions, so the check
    looks for the token in the stored ``CREATE TABLE`` SQL with no
    normalisation needed (the token contains no whitespace).
    """
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cooldowns'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False
    return "whisparr_v2_episode" in str(row[0] or "")


async def _cooldowns_has_search_kind_check(db: aiosqlite.Connection) -> bool:
    """Return whether the ``cooldowns`` table carries the v15 CHECK clause.

    The fresh-install DDL emits ``CHECK(search_kind IN ('missing', ...))``
    (sqlite stores whatever whitespace was used).  Strip all whitespace
    from the stored SQL and look for the compact form so either
    formatting path is recognised.
    """
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cooldowns'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False
    compact = "".join(str(row[0] or "").split())
    return "CHECK(search_kindIN" in compact


async def _ensure_v3_indexes(db: aiosqlite.Connection) -> None:
    """Create indexes that depend on post-v2 columns.

    Runs after the main schema DDL and after any migrations, so every
    column each index references is guaranteed to exist.  Kept under
    the ``v3`` name for historical continuity; in practice it now
    carries any post-schema index that cannot live in ``_SCHEMA_SQL``
    because its column was added by a later migration.
    """
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_log_cycle ON search_log(cycle_id, timestamp DESC)"
    )
    # search_kind arrived in v14; the column will exist here whether
    # this DB came in fresh (the DDL in _SCHEMA_SQL creates it) or
    # migrated up (_migrate_to_v14 adds it via ALTER TABLE).
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cooldowns_search_kind "
        "ON cooldowns(instance_id, search_kind)"
    )


async def _migrate_to_v17(db: aiosqlite.Connection) -> None:
    """Add ``upgrade_series_window_size`` for per-instance Sonarr/Whisparr-v2 tuning.

    Sonarr and Whisparr v2 use a windowed series rotation in the upgrade
    pool fetcher: each cycle samples up to N monitored series, advancing
    the offset by N every cycle.  N was a module constant (5) until this
    migration.  Exposing it as a per-instance column lets users with very
    large libraries trade per-cycle *arr load for faster rotation
    coverage without forking the code.

    Default of 5 preserves the old behaviour for every existing instance.
    The CHECK clause caps the value at 100 to prevent a runaway value
    from triggering a single cycle that fetches every series at once.
    """
    if not await _column_exists(db, "instances", "upgrade_series_window_size"):
        await db.execute(
            "ALTER TABLE instances ADD COLUMN upgrade_series_window_size INTEGER NOT NULL DEFAULT 5"
        )


async def _column_exists(db: aiosqlite.Connection, table_name: str, column_name: str) -> bool:
    """Return whether *column_name* exists on *table_name*."""
    async with db.execute(f"PRAGMA table_info({table_name})") as cur:  # noqa: S608  # nosec B608
        rows = await cur.fetchall()
    return any(row[1] == column_name for row in rows)

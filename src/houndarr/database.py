"""SQLite database connection, schema initialization, and migration helpers."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version — bump when adding new migrations
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 4

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    type                 TEXT    NOT NULL CHECK(type IN ('sonarr', 'radarr')),
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
);

CREATE TABLE IF NOT EXISTS cooldowns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL,
    item_type   TEXT    NOT NULL CHECK(item_type IN ('episode', 'movie')),
    searched_at TEXT    NOT NULL,
    UNIQUE(instance_id, item_id, item_type)
);

CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup
    ON cooldowns(instance_id, item_type, searched_at);

CREATE TABLE IF NOT EXISTS search_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
    item_id     INTEGER,
    item_type   TEXT    CHECK(item_type IN ('episode', 'movie')),
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
    """Yield a database connection with WAL mode and foreign keys enabled."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create tables and run migrations if needed."""
    async with get_db() as db:
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

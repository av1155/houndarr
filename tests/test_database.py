"""Tests for database layer: schema, settings helpers."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from houndarr.database import get_db, get_setting, init_db, set_db_path, set_setting


@pytest.mark.asyncio()
async def test_schema_created(db: None) -> None:
    """DB init should create all expected tables."""
    async with (
        get_db() as conn,
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") as cur,
    ):
        tables = {row["name"] async for row in cur}

    assert "settings" in tables
    assert "instances" in tables
    assert "cooldowns" in tables
    assert "search_log" in tables


@pytest.mark.asyncio()
async def test_schema_version_set(db: None) -> None:
    """Schema version should be set after init."""
    version = await get_setting("schema_version")
    assert version == "3"


@pytest.mark.asyncio()
async def test_search_log_and_instance_v3_columns_exist(db: None) -> None:
    """v3 schema includes cycle context plus cutoff-specific throttling columns."""
    async with (
        get_db() as conn,
        conn.execute("PRAGMA table_info(search_log)") as search_log_cur,
        conn.execute("PRAGMA table_info(instances)") as instances_cur,
    ):
        search_log_columns = {row[1] async for row in search_log_cur}
        instance_columns = {row[1] async for row in instances_cur}

    assert "item_label" in search_log_columns
    assert "search_kind" in search_log_columns
    assert "cycle_id" in search_log_columns
    assert "cycle_trigger" in search_log_columns
    assert "cutoff_cooldown_days" in instance_columns
    assert "cutoff_hourly_cap" in instance_columns


@pytest.mark.asyncio()
async def test_init_db_migrates_v1_schema_to_v3(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=1 databases to v3."""
    db_path = tmp_path / "migrate-v1.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '1');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 10,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 15,
                hourly_cap INTEGER NOT NULL DEFAULT 20,
                cooldown_days INTEGER NOT NULL DEFAULT 7,
                unreleased_delay_hrs INTEGER NOT NULL DEFAULT 24,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 5,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER,
                item_id INTEGER,
                item_type TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    async with (
        get_db() as conn,
        conn.execute("PRAGMA table_info(search_log)") as search_log_cur,
        conn.execute("PRAGMA table_info(instances)") as instances_cur,
    ):
        search_log_columns = {row[1] async for row in search_log_cur}
        instance_columns = {row[1] async for row in instances_cur}

    assert await get_setting("schema_version") == "3"
    assert "item_label" in search_log_columns
    assert "search_kind" in search_log_columns
    assert "cycle_id" in search_log_columns
    assert "cycle_trigger" in search_log_columns
    assert "cutoff_cooldown_days" in instance_columns
    assert "cutoff_hourly_cap" in instance_columns


@pytest.mark.asyncio()
async def test_init_db_migrates_v2_schema_to_v3(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=2 databases to v3."""
    db_path = tmp_path / "migrate-v2.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '2');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 2,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
                hourly_cap INTEGER NOT NULL DEFAULT 4,
                cooldown_days INTEGER NOT NULL DEFAULT 14,
                unreleased_delay_hrs INTEGER NOT NULL DEFAULT 36,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
                cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
                cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER,
                item_id INTEGER,
                item_type TEXT,
                search_kind TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(search_log)") as cur:
            search_log_columns = {row[1] async for row in cur}

    assert await get_setting("schema_version") == "3"
    assert "cycle_id" in search_log_columns
    assert "cycle_trigger" in search_log_columns


@pytest.mark.asyncio()
async def test_set_and_get_setting(db: None) -> None:
    """set_setting / get_setting round-trip."""
    await set_setting("test_key", "hello")
    value = await get_setting("test_key")
    assert value == "hello"


@pytest.mark.asyncio()
async def test_get_setting_default(db: None) -> None:
    """get_setting returns default when key not found."""
    value = await get_setting("nonexistent_key", default="fallback")
    assert value == "fallback"


@pytest.mark.asyncio()
async def test_set_setting_upsert(db: None) -> None:
    """set_setting overwrites existing value."""
    await set_setting("upsert_key", "first")
    await set_setting("upsert_key", "second")
    value = await get_setting("upsert_key")
    assert value == "second"

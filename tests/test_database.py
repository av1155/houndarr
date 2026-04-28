"""Tests for database layer: schema, settings helpers."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from houndarr.database import get_db, init_db, set_db_path
from houndarr.repositories.search_log import purge_old_logs
from houndarr.repositories.settings import get_setting, set_setting


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
    assert version == "17"


@pytest.mark.asyncio()
async def test_search_log_and_instance_v3_columns_exist(db: None) -> None:
    """Schema includes cycle context and instance strategy/throttling columns."""
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
    assert "sonarr_search_mode" in instance_columns
    assert "lidarr_search_mode" in instance_columns
    assert "readarr_search_mode" in instance_columns
    assert "whisparr_v2_search_mode" in instance_columns
    assert "post_release_grace_hrs" in instance_columns


@pytest.mark.asyncio()
async def test_init_db_migrates_v1_schema_to_v3(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=1 databases to v7."""
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

    assert await get_setting("schema_version") == "17"
    assert "item_label" in search_log_columns
    assert "search_kind" in search_log_columns
    assert "cycle_id" in search_log_columns
    assert "cycle_trigger" in search_log_columns
    assert "cutoff_cooldown_days" in instance_columns
    assert "cutoff_hourly_cap" in instance_columns
    assert "sonarr_search_mode" in instance_columns
    assert "lidarr_search_mode" in instance_columns
    assert "readarr_search_mode" in instance_columns
    assert "whisparr_v2_search_mode" in instance_columns
    assert "post_release_grace_hrs" in instance_columns
    assert "unreleased_delay_hrs" not in instance_columns


@pytest.mark.asyncio()
async def test_init_db_migrates_v2_schema_to_v4(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=2 databases to v7."""
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

    assert await get_setting("schema_version") == "17"
    assert "cycle_id" in search_log_columns
    assert "cycle_trigger" in search_log_columns

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {row[1] async for row in cur}
    assert "sonarr_search_mode" in instance_columns
    assert "lidarr_search_mode" in instance_columns
    assert "readarr_search_mode" in instance_columns
    assert "whisparr_v2_search_mode" in instance_columns
    assert "post_release_grace_hrs" in instance_columns
    assert "unreleased_delay_hrs" not in instance_columns


@pytest.mark.asyncio()
async def test_init_db_migrates_v3_schema_to_v4(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=3 databases to v7."""
    db_path = tmp_path / "migrate-v3.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '3');

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
                cycle_id TEXT,
                cycle_trigger TEXT,
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

    assert await get_setting("schema_version") == "17"
    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {row[1] async for row in cur}
    assert "sonarr_search_mode" in instance_columns
    assert "lidarr_search_mode" in instance_columns
    assert "readarr_search_mode" in instance_columns
    assert "whisparr_v2_search_mode" in instance_columns
    assert "post_release_grace_hrs" in instance_columns
    assert "unreleased_delay_hrs" not in instance_columns


@pytest.mark.asyncio()
async def test_init_db_migrates_v4_schema_to_v6(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=4 databases to v7."""
    db_path = tmp_path / "migrate-v4.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '4');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('sonarr', 'radarr')),
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
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO instances (id, name, type, url, sonarr_search_mode)
            VALUES (1, 'Test Sonarr', 'sonarr', 'http://sonarr:8989', 'episode');

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK(item_type IN ('episode', 'movie')),
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)
            VALUES (1, 42, 'episode', '2024-06-01T12:00:00.000Z');

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
                item_id INTEGER,
                item_type TEXT CHECK(item_type IN ('episode', 'movie')),
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO search_log (instance_id, item_id, item_type, action, timestamp)
            VALUES (1, 42, 'episode', 'searched', '2024-06-01T12:00:00.000Z');
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    assert await get_setting("schema_version") == "17"

    async with get_db() as conn:
        # Verify new columns exist
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {row[1] async for row in cur}
        assert "lidarr_search_mode" in instance_columns
        assert "readarr_search_mode" in instance_columns
        assert "whisparr_v2_search_mode" in instance_columns
        assert "post_release_grace_hrs" in instance_columns
        assert "unreleased_delay_hrs" not in instance_columns

        # Verify existing data survived migration
        async with conn.execute("SELECT name, type FROM instances WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["name"] == "Test Sonarr"
        assert row["type"] == "sonarr"

        # Verify 36 → 6 default migration
        async with conn.execute("SELECT post_release_grace_hrs FROM instances WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 6

        async with conn.execute("SELECT item_id, item_type FROM cooldowns WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["item_id"] == 42
        assert row["item_type"] == "episode"

        async with conn.execute("SELECT item_id, action FROM search_log WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["item_id"] == 42
        assert row["action"] == "searched"

        # Verify new type values are accepted in CHECK constraints
        await conn.execute(
            "INSERT INTO instances (id, name, type, url) VALUES (2, 'Lidarr', 'lidarr', 'http://lidarr:8686')"
        )
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at) "
            "VALUES (2, 1, 'album', '2024-06-01T12:00:00.000Z')"
        )
        await conn.execute(
            "INSERT INTO search_log (instance_id, item_id, item_type, action, timestamp) "
            "VALUES (2, 1, 'album', 'searched', '2024-06-01T12:00:00.000Z')"
        )
        await conn.commit()


@pytest.mark.asyncio()
async def test_init_db_migrates_v5_schema_to_v6(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=5 databases to v7."""
    db_path = tmp_path / "migrate-v5.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '5');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN (
                    'sonarr','radarr','lidarr','readarr','whisparr'
                )),
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
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO instances (
                id, name, type, url, unreleased_delay_hrs
            )
            VALUES (1, 'Default Sonarr', 'sonarr', 'http://sonarr:8989', 36);
            INSERT INTO instances (
                id, name, type, url, unreleased_delay_hrs
            )
            VALUES (2, 'Custom Radarr', 'radarr', 'http://radarr:7878', 48);

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK(item_type IN (
                    'episode','movie','album','book','whisparr_episode'
                )),
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER
                    REFERENCES instances(id) ON DELETE SET NULL,
                item_id INTEGER,
                item_type TEXT CHECK(item_type IN (
                    'episode','movie','album','book','whisparr_episode'
                )),
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    assert await get_setting("schema_version") == "17"

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {row[1] async for row in cur}
        assert "post_release_grace_hrs" in instance_columns
        assert "unreleased_delay_hrs" not in instance_columns

        # Default value (36) migrated to 6
        async with conn.execute("SELECT post_release_grace_hrs FROM instances WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 6

        # Custom value (48) preserved as-is
        async with conn.execute("SELECT post_release_grace_hrs FROM instances WHERE id = 2") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 48


@pytest.mark.asyncio()
async def test_init_db_migrates_v6_schema_to_v7(tmp_path: Path) -> None:
    """init_db should migrate existing schema_version=6 databases to v7."""
    db_path = tmp_path / "migrate-v6.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '6');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN (
                    'sonarr','radarr','lidarr','readarr','whisparr'
                )),
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 2,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
                hourly_cap INTEGER NOT NULL DEFAULT 4,
                cooldown_days INTEGER NOT NULL DEFAULT 14,
                post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
                cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
                cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO instances (id, name, type, url)
            VALUES (1, 'Test Sonarr', 'sonarr', 'http://sonarr:8989');

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK(item_type IN (
                    'episode','movie','album','book','whisparr_episode'
                )),
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER
                    REFERENCES instances(id) ON DELETE SET NULL,
                item_id INTEGER,
                item_type TEXT CHECK(item_type IN (
                    'episode','movie','album','book','whisparr_episode'
                )),
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    assert await get_setting("schema_version") == "17"

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {row[1] async for row in cur}
        assert "queue_limit" in instance_columns

        # Default value should be 0
        async with conn.execute("SELECT queue_limit FROM instances WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0


@pytest.mark.asyncio()
async def test_init_db_self_heals_v9_and_v10_when_version_already_current(
    tmp_path: Path,
) -> None:
    """init_db should add missing v9 columns and expand v10 CHECK constraints.

    Regression test for a scenario where the version was bumped but the
    ALTER TABLE statements did not persist (e.g. interrupted WAL checkpoint
    or hot-reload race during development).  Also verifies the v10 self-heal:
    ``whisparr`` rows become ``whisparr_v2``, and ``whisparr_v3`` /
    ``whisparr_v3_movie`` are accepted by the updated CHECK constraints.
    """
    db_path = tmp_path / "corrupted-v9.db"

    # Build a schema-8-shaped table but stamp version as current to simulate
    # the corrupted state.
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '10');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN (
                    'sonarr','radarr','lidarr','readarr','whisparr'
                )),
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 2,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
                hourly_cap INTEGER NOT NULL DEFAULT 4,
                cooldown_days INTEGER NOT NULL DEFAULT 14,
                post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
                queue_limit INTEGER NOT NULL DEFAULT 0,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
                cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
                cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_enabled INTEGER NOT NULL DEFAULT 0,
                upgrade_batch_size INTEGER NOT NULL DEFAULT 5,
                upgrade_cooldown_days INTEGER NOT NULL DEFAULT 30,
                upgrade_hourly_cap INTEGER NOT NULL DEFAULT 2,
                upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_item_offset INTEGER NOT NULL DEFAULT 0,
                upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            -- Seed a row with the old 'whisparr' type to verify v10 rename.
            INSERT INTO instances (name, type, url)
            VALUES ('Old Whisparr', 'whisparr', 'http://whisparr:6969');

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK(item_type IN (
                    'episode','movie','album','book','whisparr_episode'
                )),
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER
                    REFERENCES instances(id) ON DELETE SET NULL,
                item_id INTEGER,
                item_type TEXT CHECK(item_type IN (
                    'episode','movie','album','book','whisparr_episode'
                )),
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    assert await get_setting("schema_version") == "17"

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {row[1] async for row in cur}
        assert "missing_page_offset" in instance_columns
        assert "cutoff_page_offset" in instance_columns

        # v10 self-heal: old 'whisparr' row should be renamed to 'whisparr_v2'.
        async with conn.execute("SELECT type FROM instances WHERE name = 'Old Whisparr'") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "whisparr_v2"

        # v10 self-heal: new 'whisparr_v3' type accepted by CHECK constraint.
        await conn.execute(
            "INSERT INTO instances (name, type, url)"
            " VALUES ('v10 guard', 'whisparr_v3', 'http://test')"
        )

        # v10 self-heal: 'whisparr_v3_movie' accepted in cooldowns CHECK.
        v3_id_row = await conn.execute("SELECT id FROM instances WHERE name = 'v10 guard'")
        v3_id = (await v3_id_row.fetchone())[0]
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, 1, 'whisparr_v3_movie', '2024-01-01T00:00:00Z')",
            (v3_id,),
        )


@pytest.mark.asyncio()
async def test_init_db_self_heals_v12_when_column_missing(tmp_path: Path) -> None:
    """init_db must add ``search_order`` when the version is stamped at 12 but
    the column is missing (simulates a partially-applied migration from an
    interrupted hot-reload or WAL checkpoint).
    """
    db_path = tmp_path / "corrupt-v12.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '12');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN (
                    'radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3'
                )),
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 2,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
                hourly_cap INTEGER NOT NULL DEFAULT 4,
                cooldown_days INTEGER NOT NULL DEFAULT 14,
                post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
                queue_limit INTEGER NOT NULL DEFAULT 0,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
                cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
                cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_enabled INTEGER NOT NULL DEFAULT 0,
                upgrade_batch_size INTEGER NOT NULL DEFAULT 1,
                upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
                upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1,
                upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_item_offset INTEGER NOT NULL DEFAULT 0,
                upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
                missing_page_offset INTEGER NOT NULL DEFAULT 1,
                cutoff_page_offset INTEGER NOT NULL DEFAULT 1,
                allowed_time_window TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO instances (name, type, url)
            VALUES ('Ghost Sonarr', 'sonarr', 'http://sonarr:8989');

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER,
                item_id INTEGER,
                item_type TEXT,
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            columns = {row[1] async for row in cur}
        assert "search_order" in columns

        async with conn.execute(
            "SELECT search_order FROM instances WHERE name = 'Ghost Sonarr'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "chronological"


@pytest.mark.asyncio()
async def test_init_db_is_idempotent_on_healthy_v12(tmp_path: Path) -> None:
    """Running init_db twice on a healthy v12 database must not error or drift."""
    db_path = tmp_path / "healthy-v12.db"

    set_db_path(str(db_path))
    await init_db()  # fresh install
    first_version = await get_setting("schema_version")

    # Second call: should be a no-op through the self-heal branch.
    await init_db()
    assert await get_setting("schema_version") == first_version == "17"

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            columns = {row[1] async for row in cur}
    # Spot-check a few expected columns to ensure nothing was dropped.
    for col in ("search_order", "allowed_time_window", "missing_page_offset"):
        assert col in columns


@pytest.mark.asyncio()
async def test_migrate_to_v12_adds_search_order_column(tmp_path: Path) -> None:
    """init_db on a v11-shaped DB should add ``search_order`` with default ``chronological``."""
    db_path = tmp_path / "migrate-v11-to-v12.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '11');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN (
                    'radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3'
                )),
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 2,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
                hourly_cap INTEGER NOT NULL DEFAULT 4,
                cooldown_days INTEGER NOT NULL DEFAULT 14,
                post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
                queue_limit INTEGER NOT NULL DEFAULT 0,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
                cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
                cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_enabled INTEGER NOT NULL DEFAULT 0,
                upgrade_batch_size INTEGER NOT NULL DEFAULT 1,
                upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
                upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1,
                upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_item_offset INTEGER NOT NULL DEFAULT 0,
                upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
                missing_page_offset INTEGER NOT NULL DEFAULT 1,
                cutoff_page_offset INTEGER NOT NULL DEFAULT 1,
                allowed_time_window TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO instances (name, type, url)
            VALUES ('Pre-v12 Sonarr', 'sonarr', 'http://sonarr:8989');

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER,
                item_id INTEGER,
                item_type TEXT,
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    assert await get_setting("schema_version") == "17"

    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            columns = {row[1] async for row in cur}
        assert "search_order" in columns

        async with conn.execute(
            "SELECT search_order FROM instances WHERE name = 'Pre-v12 Sonarr'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "chronological"


@pytest.mark.asyncio()
async def test_cooldowns_search_kind_check_enforced(db: None) -> None:  # noqa: ARG001
    """Every initialised DB rejects search_kind values outside the CHECK.

    Fresh installs pick this up from ``_SCHEMA_SQL``; databases that
    migrated through v14 used to silently accept any string (SQLite
    cannot attach a CHECK via ALTER TABLE ADD COLUMN).  v15 rebuilds
    the table so the invariant holds everywhere.
    """
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
            " VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'fake-key')"
        )
        await conn.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO cooldowns"
                " (instance_id, item_id, item_type, search_kind, searched_at)"
                " VALUES (1, 999, 'episode', 'not_a_kind', '2024-01-01T00:00:00.000Z')"
            )


@pytest.mark.asyncio()
async def test_migrate_to_v15_coerces_invalid_search_kind(tmp_path: Path) -> None:
    """Pre-existing rows with an invalid ``search_kind`` are coerced to
    ``'missing'`` during the v14→v15 rebuild so the new CHECK does not
    reject them.  The app itself never writes a bad kind, but a
    corrupted snapshot or a hand-edited DB might; the defensive CASE
    in the INSERT SELECT keeps the rebuild from failing."""
    db_path = tmp_path / "migrate-v14-to-v15.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        # Minimal v14-shaped schema.  The v9-v12 self-heal migrations
        # only run when their target columns are absent, so the
        # instances table must already carry every post-v8 column; we
        # mirror the v12-test's full column list below.
        await conn.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings (key, value) VALUES ('schema_version', '14');

            CREATE TABLE instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN (
                    'radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3'
                )),
                url TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL DEFAULT '',
                batch_size INTEGER NOT NULL DEFAULT 2,
                sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
                hourly_cap INTEGER NOT NULL DEFAULT 4,
                cooldown_days INTEGER NOT NULL DEFAULT 14,
                post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
                queue_limit INTEGER NOT NULL DEFAULT 0,
                cutoff_enabled INTEGER NOT NULL DEFAULT 0,
                cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
                cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
                cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
                sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_enabled INTEGER NOT NULL DEFAULT 0,
                upgrade_batch_size INTEGER NOT NULL DEFAULT 1,
                upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
                upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1,
                upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
                upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',
                upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
                upgrade_item_offset INTEGER NOT NULL DEFAULT 0,
                upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
                missing_page_offset INTEGER NOT NULL DEFAULT 1,
                cutoff_page_offset INTEGER NOT NULL DEFAULT 1,
                allowed_time_window TEXT NOT NULL DEFAULT '',
                search_order TEXT NOT NULL DEFAULT 'chronological',
                monitored_total INTEGER NOT NULL DEFAULT 0,
                unreleased_count INTEGER NOT NULL DEFAULT 0,
                snapshot_refreshed_at TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
                updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            CREATE TABLE cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                    REFERENCES instances(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                search_kind TEXT NOT NULL DEFAULT 'missing',
                searched_at TEXT NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );

            CREATE TABLE search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER,
                item_id INTEGER,
                item_type TEXT,
                search_kind TEXT,
                cycle_id TEXT,
                cycle_trigger TEXT,
                item_label TEXT,
                action TEXT NOT NULL,
                reason TEXT,
                message TEXT,
                timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
            );

            INSERT INTO instances (id, name, type, url)
            VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989');

            INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)
            VALUES (1, 100, 'episode', 'missing', '2024-01-01T00:00:00.000Z'),
                   (1, 101, 'episode', 'cutoff',  '2024-01-01T00:00:00.000Z'),
                   (1, 102, 'episode', 'upgrade', '2024-01-01T00:00:00.000Z'),
                   (1, 103, 'episode', 'BOGUS',   '2024-01-01T00:00:00.000Z');
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    assert await get_setting("schema_version") == "17"

    async with get_db() as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        async with conn.execute(
            "SELECT item_id, search_kind FROM cooldowns ORDER BY item_id"
        ) as cur:
            rows = [(int(r[0]), str(r[1])) async for r in cur]
        assert rows == [
            (100, "missing"),
            (101, "cutoff"),
            (102, "upgrade"),
            (103, "missing"),
        ]


# ---------------------------------------------------------------------------
# Historical migration matrix
# ---------------------------------------------------------------------------
#
# Every released schema version must migrate cleanly to current with realistic
# data, including ``whisparr_episode`` cooldown rows from any v1.1.0+ install
# that ever ran a Whisparr instance.  The fixtures below emit the
# contemporaneous DDL for each schema version (literal SQL, no module
# constants) so the tests are stable against future changes to
# ``_ITEM_TYPES`` / ``_INSTANCE_TYPES``.


def _seed_v4_shaped_db() -> str:
    """v4-shaped DB (~v1.0.x): pre-Whisparr era.

    Only ``sonarr`` and ``radarr`` instance types; only ``episode`` and
    ``movie`` item_types.  Drives the longest possible migration chain
    (v5 → v17) and verifies that v10's rebuild preserves every cooldown
    row when no transaction-leak from v6 exists at v4 (since v6 has not
    run yet at the start of this chain — but v6 will run mid-chain).
    """
    return """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO settings (key, value) VALUES ('schema_version', '4');

    CREATE TABLE instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('sonarr','radarr')),
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
        sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
        updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );

    CREATE TABLE cooldowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN ('episode','movie')),
        searched_at TEXT NOT NULL,
        UNIQUE(instance_id, item_id, item_type)
    );

    CREATE TABLE search_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
        item_id INTEGER,
        item_type TEXT CHECK(item_type IN ('episode','movie')),
        search_kind TEXT,
        cycle_id TEXT,
        cycle_trigger TEXT,
        item_label TEXT,
        action TEXT NOT NULL,
        reason TEXT,
        message TEXT,
        timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );
    """


def _seed_v5_shaped_db() -> str:
    """v5-shaped DB (~v1.1.x): the earliest version where ``whisparr_episode``
    item_type values exist.  Drives the longest realistic migration chain
    (v6 → v17) including v6's column drop, v10's rebuild, v15's rebuild,
    and v16's rename.
    """
    return """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO settings (key, value) VALUES ('schema_version', '5');

    CREATE TABLE instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN (
            'sonarr','radarr','lidarr','readarr','whisparr'
        )),
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
        sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
        readarr_search_mode TEXT NOT NULL DEFAULT 'book',
        whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
        updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );

    CREATE TABLE cooldowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode'
        )),
        searched_at TEXT NOT NULL,
        UNIQUE(instance_id, item_id, item_type)
    );

    CREATE TABLE search_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
        item_id INTEGER,
        item_type TEXT CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode'
        )),
        search_kind TEXT,
        cycle_id TEXT,
        cycle_trigger TEXT,
        item_label TEXT,
        action TEXT NOT NULL,
        reason TEXT,
        message TEXT,
        timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );
    """


def _seed_v7_shaped_db() -> str:
    """v7-shaped DB (~v1.2.x .. v1.5.0): v6 already ran (post_release_grace_hrs
    in place of unreleased_delay_hrs) plus v7's ``queue_limit``.  No upgrade_*
    columns yet (v8) and no page_offset (v9).
    """
    return """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO settings (key, value) VALUES ('schema_version', '7');

    CREATE TABLE instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN (
            'sonarr','radarr','lidarr','readarr','whisparr'
        )),
        url TEXT NOT NULL,
        encrypted_api_key TEXT NOT NULL DEFAULT '',
        batch_size INTEGER NOT NULL DEFAULT 2,
        sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
        hourly_cap INTEGER NOT NULL DEFAULT 4,
        cooldown_days INTEGER NOT NULL DEFAULT 14,
        post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
        queue_limit INTEGER NOT NULL DEFAULT 0,
        cutoff_enabled INTEGER NOT NULL DEFAULT 0,
        cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
        cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
        cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
        sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
        readarr_search_mode TEXT NOT NULL DEFAULT 'book',
        whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
        updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );

    CREATE TABLE cooldowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode'
        )),
        searched_at TEXT NOT NULL,
        UNIQUE(instance_id, item_id, item_type)
    );

    CREATE TABLE search_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
        item_id INTEGER,
        item_type TEXT CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode'
        )),
        search_kind TEXT,
        cycle_id TEXT,
        cycle_trigger TEXT,
        item_label TEXT,
        action TEXT NOT NULL,
        reason TEXT,
        message TEXT,
        timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );
    """


def _seed_v8_shaped_db() -> str:
    """v8-shaped DB (~v1.6.0 .. v1.6.2): v7 plus the upgrade_* columns."""
    return (
        _seed_v7_shaped_db()
        .replace(
            "INSERT INTO settings (key, value) VALUES ('schema_version', '7');",
            "INSERT INTO settings (key, value) VALUES ('schema_version', '8');",
        )
        .replace(
            "whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',",
            "whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',\n        "
            "upgrade_enabled INTEGER NOT NULL DEFAULT 0,\n        "
            "upgrade_batch_size INTEGER NOT NULL DEFAULT 1,\n        "
            "upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,\n        "
            "upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1,\n        "
            "upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',\n        "
            "upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',\n        "
            "upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',\n        "
            "upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',\n        "
            "upgrade_item_offset INTEGER NOT NULL DEFAULT 0,\n        "
            "upgrade_series_offset INTEGER NOT NULL DEFAULT 0,",
        )
    )


def _seed_v9_shaped_db() -> str:
    """Return the DDL for a v9-shaped database (~v1.6.x).

    v9 still uses the v5 ``_INSTANCE_TYPES`` (singular ``whisparr``) and the
    v5 ``_ITEM_TYPES`` (allows ``whisparr_episode``, no ``whisparr_v3_movie``).
    Adds ``missing_page_offset`` / ``cutoff_page_offset`` from v9.
    """
    return """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO settings (key, value) VALUES ('schema_version', '9');

    CREATE TABLE instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN (
            'sonarr','radarr','lidarr','readarr','whisparr'
        )),
        url TEXT NOT NULL,
        encrypted_api_key TEXT NOT NULL DEFAULT '',
        batch_size INTEGER NOT NULL DEFAULT 2,
        sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
        hourly_cap INTEGER NOT NULL DEFAULT 4,
        cooldown_days INTEGER NOT NULL DEFAULT 14,
        post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
        queue_limit INTEGER NOT NULL DEFAULT 0,
        cutoff_enabled INTEGER NOT NULL DEFAULT 0,
        cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
        cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
        cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
        sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
        readarr_search_mode TEXT NOT NULL DEFAULT 'book',
        whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
        upgrade_enabled INTEGER NOT NULL DEFAULT 0,
        upgrade_batch_size INTEGER NOT NULL DEFAULT 1,
        upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
        upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1,
        upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
        upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',
        upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
        upgrade_item_offset INTEGER NOT NULL DEFAULT 0,
        upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
        missing_page_offset INTEGER NOT NULL DEFAULT 1,
        cutoff_page_offset INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
        updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );

    CREATE TABLE cooldowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode'
        )),
        searched_at TEXT NOT NULL,
        UNIQUE(instance_id, item_id, item_type)
    );

    CREATE TABLE search_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
        item_id INTEGER,
        item_type TEXT CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode'
        )),
        search_kind TEXT,
        cycle_id TEXT,
        cycle_trigger TEXT,
        item_label TEXT,
        action TEXT NOT NULL,
        reason TEXT,
        message TEXT,
        timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );
    """


def _seed_v10_shaped_db() -> str:
    """v10-shaped DB (~v1.7.0): type CHECK rebuilt to whisparr_v2/whisparr_v3."""
    return """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO settings (key, value) VALUES ('schema_version', '10');

    CREATE TABLE instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN (
            'radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3'
        )),
        url TEXT NOT NULL,
        encrypted_api_key TEXT NOT NULL DEFAULT '',
        batch_size INTEGER NOT NULL DEFAULT 2,
        sleep_interval_mins INTEGER NOT NULL DEFAULT 30,
        hourly_cap INTEGER NOT NULL DEFAULT 4,
        cooldown_days INTEGER NOT NULL DEFAULT 14,
        post_release_grace_hrs INTEGER NOT NULL DEFAULT 6,
        queue_limit INTEGER NOT NULL DEFAULT 0,
        cutoff_enabled INTEGER NOT NULL DEFAULT 0,
        cutoff_batch_size INTEGER NOT NULL DEFAULT 1,
        cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
        cutoff_hourly_cap INTEGER NOT NULL DEFAULT 1,
        sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
        readarr_search_mode TEXT NOT NULL DEFAULT 'book',
        whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
        upgrade_enabled INTEGER NOT NULL DEFAULT 0,
        upgrade_batch_size INTEGER NOT NULL DEFAULT 1,
        upgrade_cooldown_days INTEGER NOT NULL DEFAULT 90,
        upgrade_hourly_cap INTEGER NOT NULL DEFAULT 1,
        upgrade_sonarr_search_mode TEXT NOT NULL DEFAULT 'episode',
        upgrade_lidarr_search_mode TEXT NOT NULL DEFAULT 'album',
        upgrade_readarr_search_mode TEXT NOT NULL DEFAULT 'book',
        upgrade_whisparr_search_mode TEXT NOT NULL DEFAULT 'episode',
        upgrade_item_offset INTEGER NOT NULL DEFAULT 0,
        upgrade_series_offset INTEGER NOT NULL DEFAULT 0,
        missing_page_offset INTEGER NOT NULL DEFAULT 1,
        cutoff_page_offset INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z',
        updated_at TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );

    CREATE TABLE cooldowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode','whisparr_v3_movie'
        )),
        searched_at TEXT NOT NULL,
        UNIQUE(instance_id, item_id, item_type)
    );

    CREATE TABLE search_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER REFERENCES instances(id) ON DELETE SET NULL,
        item_id INTEGER,
        item_type TEXT CHECK(item_type IN (
            'episode','movie','album','book','whisparr_episode','whisparr_v3_movie'
        )),
        search_kind TEXT,
        cycle_id TEXT,
        cycle_trigger TEXT,
        item_label TEXT,
        action TEXT NOT NULL,
        reason TEXT,
        message TEXT,
        timestamp TEXT NOT NULL DEFAULT '2024-01-01T00:00:00.000Z'
    );
    """


def _seed_v11_shaped_db() -> str:
    """v11-shaped DB (~v1.8.0): adds allowed_time_window."""
    return (
        _seed_v10_shaped_db()
        .replace(
            "INSERT INTO settings (key, value) VALUES ('schema_version', '10');",
            "INSERT INTO settings (key, value) VALUES ('schema_version', '11');",
        )
        .replace(
            "missing_page_offset INTEGER NOT NULL DEFAULT 1,",
            "missing_page_offset INTEGER NOT NULL DEFAULT 1,\n        "
            "allowed_time_window TEXT NOT NULL DEFAULT '',",
        )
    )


def _seed_v12_shaped_db() -> str:
    """v12-shaped DB (~v1.9.0): adds search_order.

    This is the regression-of-record fixture: a Whisparr v2 user on v1.9.0
    upgrading to next-release hits ``_migrate_to_v15`` first, which used to
    rebuild ``cooldowns`` with a CHECK that no longer allowed
    ``whisparr_episode`` rows.
    """
    return (
        _seed_v11_shaped_db()
        .replace(
            "INSERT INTO settings (key, value) VALUES ('schema_version', '11');",
            "INSERT INTO settings (key, value) VALUES ('schema_version', '12');",
        )
        .replace(
            "allowed_time_window TEXT NOT NULL DEFAULT '',",
            "allowed_time_window TEXT NOT NULL DEFAULT '',\n        "
            "search_order TEXT NOT NULL DEFAULT 'chronological',",
        )
    )


def _whisparr_v2_seed_inserts(version: int) -> str:
    """INSERTs that exercise the rename path: a Whisparr v2 instance plus
    cooldowns/search_log rows carrying the pre-v16 ``whisparr_episode`` value.
    """
    instance_type = "whisparr" if version <= 9 else "whisparr_v2"
    return f"""
    INSERT INTO instances (id, name, type, url) VALUES
        (1, 'Sonarr Test', 'sonarr', 'http://sonarr:8989'),
        (2, 'Whisparr v2 Test', '{instance_type}', 'http://whisparr:6969');

    INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at) VALUES
        (1, 100, 'episode',          '2024-06-01T00:00:00.000Z'),
        (2, 200, 'whisparr_episode', '2024-06-02T00:00:00.000Z'),
        (2, 201, 'whisparr_episode', '2024-06-03T00:00:00.000Z'),
        (2, 202, 'whisparr_episode', '2024-06-04T00:00:00.000Z');

    INSERT INTO search_log
        (instance_id, item_id, item_type, action, timestamp) VALUES
        (1, 100, 'episode',          'searched', '2024-06-01T00:00:00.000Z'),
        (2, 200, 'whisparr_episode', 'searched', '2024-06-02T00:00:00.000Z'),
        (2, 201, 'whisparr_episode', 'skipped',  '2024-06-03T00:00:00.000Z');
    """


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("source_version", "ddl_builder"),
    [
        (5, _seed_v5_shaped_db),
        (7, _seed_v7_shaped_db),
        (8, _seed_v8_shaped_db),
        (9, _seed_v9_shaped_db),
        (10, _seed_v10_shaped_db),
        (11, _seed_v11_shaped_db),
        (12, _seed_v12_shaped_db),
    ],
)
async def test_init_db_migrates_whisparr_episode_rows_through_to_current(
    tmp_path: Path,
    source_version: int,
    ddl_builder: object,
) -> None:
    """Every released schema version 9..12 must migrate to current cleanly,
    even when ``cooldowns`` and ``search_log`` carry pre-v16 ``whisparr_episode``
    rows.  Regression test for the bug where ``_migrate_to_v15`` used the
    current ``_ITEM_TYPES`` (which no longer allows ``whisparr_episode``) for
    its rebuild CHECK and crashed on the COPY before ``_migrate_to_v16`` could
    do the rename.
    """
    db_path = tmp_path / f"v{source_version}.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        # pyright doesn't understand the parametrize-supplied callable; we
        # only ever pass the four module-level functions defined above.
        await conn.executescript(ddl_builder())  # type: ignore[operator]
        await conn.executescript(_whisparr_v2_seed_inserts(source_version))
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    async with get_db() as conn:
        # 1. Schema version landed at current.
        async with conn.execute("SELECT value FROM settings WHERE key = 'schema_version'") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "17"

        # 2. The Whisparr v2 cooldown rows survived and were renamed.
        async with conn.execute(
            "SELECT item_id, item_type, search_kind FROM cooldowns"
            " WHERE instance_id = 2 ORDER BY item_id"
        ) as cur:
            cooldown_rows = [(int(r[0]), str(r[1]), str(r[2])) async for r in cur]
        assert cooldown_rows == [
            (200, "whisparr_v2_episode", "missing"),
            (201, "whisparr_v2_episode", "missing"),
            (202, "whisparr_v2_episode", "missing"),
        ]

        # 3. search_log rows likewise renamed (v16 rebuilds search_log too).
        async with conn.execute(
            "SELECT item_id, item_type, action FROM search_log"
            " WHERE instance_id = 2 ORDER BY item_id"
        ) as cur:
            log_rows = [(int(r[0]), str(r[1]), str(r[2])) async for r in cur]
        assert log_rows == [
            (200, "whisparr_v2_episode", "searched"),
            (201, "whisparr_v2_episode", "skipped"),
        ]

        # 4. Both tables now carry the v16 CHECK clause.
        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name IN ('cooldowns','search_log')"
        ) as cur:
            ddls = {str(r[0] or "") async for r in cur}
        for ddl in ddls:
            assert "whisparr_v2_episode" in ddl
            assert "whisparr_episode'" not in ddl  # the trailing quote keeps
            # 'whisparr_v2_episode' from satisfying the substring check

        # 5. cooldowns also carries the v15 search_kind CHECK clause.
        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cooldowns'"
        ) as cur:
            cooldowns_ddl = (await cur.fetchone())[0]  # type: ignore[index]
        compact = "".join(str(cooldowns_ddl or "").split())
        assert "CHECK(search_kindIN" in compact

        # 6. The Whisparr v2 instance row was migrated and the column rename
        #    landed (v10 + v16).
        async with conn.execute("SELECT type FROM instances WHERE id = 2") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "whisparr_v2"
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            instance_columns = {r[1] async for r in cur}
        assert "whisparr_v2_search_mode" in instance_columns
        assert "upgrade_whisparr_v2_search_mode" in instance_columns
        assert "upgrade_series_window_size" in instance_columns
        assert "monitored_total" in instance_columns
        assert "snapshot_refreshed_at" in instance_columns

        # 7. No leftover *_new tables from rebuild migrations.
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_new'"
        ) as cur:
            leftovers = [r[0] async for r in cur]
        assert leftovers == []

    # 8. Idempotency: running init_db a second time on the migrated DB must
    #    be a clean no-op (the self-heal block runs every startup).
    await init_db()
    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) FROM cooldowns WHERE instance_id = 2") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 3


@pytest.mark.asyncio()
async def test_init_db_migrates_v4_preserves_cooldowns_through_v10_rebuild(
    tmp_path: Path,
) -> None:
    """v4 (~v1.0.x) was the pre-Whisparr era: only sonarr/radarr instances and
    only ``episode`` / ``movie`` cooldowns.  The v4 → v17 chain runs through
    v6 (UPDATE leaves a transaction open) and v10 (rebuilds instances).
    Without the commit added at the start of v10, ``PRAGMA foreign_keys=OFF``
    silently no-ops inside the still-open transaction, FK enforcement stays
    on, and the DROP TABLE instances CASCADE-wipes every cooldown row.
    This test would have failed before the fix.
    """
    db_path = tmp_path / "v4.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(_seed_v4_shaped_db())
        await conn.executescript(
            """
            INSERT INTO instances (id, name, type, url) VALUES
                (1, 'Sonarr Test', 'sonarr', 'http://sonarr:8989'),
                (2, 'Radarr Test', 'radarr', 'http://radarr:7878');

            INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at) VALUES
                (1, 100, 'episode', '2024-06-01T00:00:00.000Z'),
                (1, 101, 'episode', '2024-06-02T00:00:00.000Z'),
                (2, 200, 'movie',   '2024-06-03T00:00:00.000Z'),
                (2, 201, 'movie',   '2024-06-04T00:00:00.000Z');

            INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp) VALUES
                (1, 100, 'episode', 'searched', '2024-06-01T00:00:00.000Z'),
                (2, 200, 'movie',   'searched', '2024-06-03T00:00:00.000Z');
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()

    async with get_db() as conn:
        async with conn.execute("SELECT value FROM settings WHERE key = 'schema_version'") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "17"

        # All four cooldown rows must survive the v10 instances rebuild.
        async with conn.execute(
            "SELECT instance_id, item_id, item_type FROM cooldowns ORDER BY item_id"
        ) as cur:
            rows = [(int(r[0]), int(r[1]), str(r[2])) async for r in cur]
        assert rows == [
            (1, 100, "episode"),
            (1, 101, "episode"),
            (2, 200, "movie"),
            (2, 201, "movie"),
        ]

        # search_log rows survive too (FK is ON DELETE SET NULL, so even
        # without the commit fix the rows would survive — but instance_id
        # would have been NULLed).  With the fix, the FK reference is
        # preserved.
        async with conn.execute(
            "SELECT instance_id, item_id FROM search_log ORDER BY item_id"
        ) as cur:
            log_rows = [(r[0], int(r[1])) async for r in cur]
        assert log_rows == [(1, 100), (2, 200)]


@pytest.mark.asyncio()
async def test_init_db_self_heals_v17_with_whisparr_v2_data(tmp_path: Path) -> None:
    """An already-migrated v17 database with whisparr_v2_episode data must
    pass through ``init_db`` unchanged: every self-heal migration's idempotency
    guard fires and returns early.
    """
    db_path = tmp_path / "healthy-v17.db"

    set_db_path(str(db_path))
    await init_db()

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
            " VALUES (1, 'Whisparr v2', 'whisparr_v2', 'http://w:6969', 'fake')"
        )
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)"
            " VALUES (1, 999, 'whisparr_v2_episode', 'missing', '2026-01-01T00:00:00.000Z')"
        )
        await conn.commit()

    # Second init_db must be a no-op: no exception, data preserved.
    await init_db()

    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_type, search_kind FROM cooldowns WHERE item_id = 999"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "whisparr_v2_episode"
        assert row[1] == "missing"

        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_new'"
        ) as cur:
            leftovers = [r[0] async for r in cur]
        assert leftovers == []


@pytest.mark.asyncio()
async def test_migrate_to_v15_recovers_from_partial_previous_run(
    tmp_path: Path,
) -> None:
    """If a previous run of ``_migrate_to_v15`` crashed after CREATE TABLE
    cooldowns_new but before DROP/RENAME, the next startup must recover by
    dropping the leftover and retrying.  The fix adds DROP TABLE IF EXISTS at
    the top of v15's body, mirroring v16.
    """
    db_path = tmp_path / "partial-v15.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        # v14-shaped schema (so v15 will run) plus a dangling cooldowns_new.
        await conn.executescript(_seed_v12_shaped_db())
        await conn.executescript(_whisparr_v2_seed_inserts(12))
        await conn.execute("UPDATE settings SET value = '14' WHERE key = 'schema_version'")
        # Add the v14 search_kind column (NOT NULL DEFAULT 'missing') without
        # the v15 CHECK so v15 will rebuild.
        await conn.execute(
            "ALTER TABLE cooldowns ADD COLUMN search_kind TEXT NOT NULL DEFAULT 'missing'"
        )
        # Simulate a leftover from a crashed previous v15 run.
        await conn.executescript(
            """
            CREATE TABLE cooldowns_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL,
                item_id     INTEGER NOT NULL,
                item_type   TEXT    NOT NULL,
                search_kind TEXT    NOT NULL DEFAULT 'missing',
                searched_at TEXT    NOT NULL,
                UNIQUE(instance_id, item_id, item_type)
            );
            """
        )
        await conn.commit()

    set_db_path(str(db_path))
    await init_db()  # must not raise "table cooldowns_new already exists"

    async with get_db() as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_new'"
        ) as cur:
            leftovers = [r[0] async for r in cur]
        assert leftovers == []
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE item_type = 'whisparr_v2_episode'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 3


@pytest.mark.asyncio()
async def test_set_and_get_setting(db: None) -> None:
    """set_setting / get_setting round-trip."""
    await set_setting("test_key", "hello")
    value = await get_setting("test_key")
    assert value == "hello"


@pytest.mark.asyncio()
async def test_get_setting_missing_key_returns_none(db: None) -> None:
    """Missing keys return ``None``; callers compose the fallback themselves."""
    value = await get_setting("nonexistent_key")
    assert value is None


@pytest.mark.asyncio()
async def test_set_setting_upsert(db: None) -> None:
    """set_setting overwrites existing value."""
    await set_setting("upsert_key", "first")
    await set_setting("upsert_key", "second")
    value = await get_setting("upsert_key")
    assert value == "second"


# ---------------------------------------------------------------------------
# Log retention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_purge_old_logs_removes_stale_rows(db: None) -> None:
    """purge_old_logs should delete rows older than the retention window."""
    async with get_db() as conn:
        # Insert two rows: one old (beyond retention), one recent
        await conn.executemany(
            "INSERT INTO search_log (instance_id, action, timestamp) VALUES (?, ?, ?)",
            [
                (None, "info", "2000-01-01T00:00:00.000Z"),  # very old - should be purged
                (None, "info", "2099-01-01T00:00:00.000Z"),  # future - should be kept
            ],
        )
        await conn.commit()

    purged = await purge_old_logs(30)
    assert purged == 1

    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) FROM search_log") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1  # only the future row remains


@pytest.mark.asyncio()
async def test_purge_old_logs_zero_retention_does_nothing(db: None) -> None:
    """purge_old_logs with retention_days=0 should not delete any rows."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, action, timestamp) VALUES (?, ?, ?)",
            (None, "info", "2000-01-01T00:00:00.000Z"),
        )
        await conn.commit()

    purged = await purge_old_logs(0)
    assert purged == 0

    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) FROM search_log") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1  # row remains


@pytest.mark.asyncio()
async def test_purge_old_logs_negative_retention_does_nothing(db: None) -> None:
    """purge_old_logs with negative retention should be a no-op."""
    purged = await purge_old_logs(-1)
    assert purged == 0


@pytest.mark.asyncio()
async def test_purge_old_logs_empty_table_returns_zero(db: None) -> None:
    """purge_old_logs on an empty table should return 0."""
    purged = await purge_old_logs(30)
    assert purged == 0

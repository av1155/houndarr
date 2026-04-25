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
    assert version == "16"


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

    assert await get_setting("schema_version") == "16"
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

    assert await get_setting("schema_version") == "16"
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

    assert await get_setting("schema_version") == "16"
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

    assert await get_setting("schema_version") == "16"

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

    assert await get_setting("schema_version") == "16"

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

    assert await get_setting("schema_version") == "16"

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

    assert await get_setting("schema_version") == "16"

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
    assert await get_setting("schema_version") == first_version == "16"

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

    assert await get_setting("schema_version") == "16"

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

    assert await get_setting("schema_version") == "16"

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


@pytest.mark.asyncio()
async def test_set_and_get_setting(db: None) -> None:
    """set_setting / get_setting round-trip."""
    await set_setting("test_key", "hello")
    value = await get_setting("test_key")
    assert value == "hello"


@pytest.mark.asyncio()
async def test_get_setting_missing_key_returns_none(db: None) -> None:
    """get_setting returns None when key not found; callers compose any fallback."""
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

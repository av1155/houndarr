"""Tests for database edge cases: purge_old_logs, get_setting, set_setting, concurrent access."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from houndarr.database import (
    _connection_factory,
    _pools,
    close_all_pools,
    get_db,
    init_db,
    set_db_path,
)
from houndarr.repositories.search_log import purge_old_logs
from houndarr.repositories.settings import get_setting, set_setting

# ---------------------------------------------------------------------------
# purge_old_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_purge_old_logs_zero_days_noop(db: None) -> None:
    """retention_days=0 disables purging and returns 0."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (action, timestamp)"
            " VALUES ('info', datetime('now', '-100 days'))",
        )
        await conn.commit()

    deleted = await purge_old_logs(0)
    assert deleted == 0


@pytest.mark.asyncio()
async def test_purge_old_logs_negative_days_noop(db: None) -> None:
    """Negative retention_days disables purging and returns 0."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (action, timestamp)"
            " VALUES ('info', datetime('now', '-100 days'))",
        )
        await conn.commit()

    deleted = await purge_old_logs(-7)
    assert deleted == 0


@pytest.mark.asyncio()
async def test_purge_old_logs_deletes_old_rows(db: None) -> None:
    """Rows older than retention_days are deleted."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (action, timestamp)"
            " VALUES ('info', datetime('now', '-60 days'))",
        )
        await conn.commit()

    deleted = await purge_old_logs(30)
    assert deleted == 1


@pytest.mark.asyncio()
async def test_purge_old_logs_preserves_recent_rows(db: None) -> None:
    """Rows newer than retention_days are preserved."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (action, timestamp)"
            " VALUES ('info', datetime('now', '-5 days'))",
        )
        await conn.commit()

    deleted = await purge_old_logs(30)
    assert deleted == 0

    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) FROM search_log") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 1


@pytest.mark.asyncio()
async def test_purge_old_logs_returns_count(db: None) -> None:
    """purge_old_logs returns the exact number of rows deleted."""
    async with get_db() as conn:
        for i in range(5):
            await conn.execute(
                "INSERT INTO search_log (action, timestamp)"
                " VALUES ('info', datetime('now', ? || ' days'))",
                (f"-{40 + i}",),
            )
        # Also insert a recent row that should survive
        await conn.execute(
            "INSERT INTO search_log (action, timestamp)"
            " VALUES ('info', datetime('now', '-2 days'))",
        )
        await conn.commit()

    deleted = await purge_old_logs(30)
    assert deleted == 5


# ---------------------------------------------------------------------------
# get_setting / set_setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_setting_missing_key_returns_none(db: None) -> None:
    """A missing key returns ``None``; callers compose any fallback themselves.

    The repository contract is ``str | None``; route callers use the
    ``(await get_setting(key)) == "1"`` / ``or <fallback>`` idiom
    when they need a default value.
    """
    assert await get_setting("nonexistent") is None


@pytest.mark.asyncio()
async def test_get_setting_existing_key(db: None) -> None:
    """An existing key returns its stored value."""
    # schema_version is set during init_db
    result = await get_setting("schema_version")
    assert result is not None
    assert int(result) > 0


@pytest.mark.asyncio()
async def test_set_setting_inserts_new(db: None) -> None:
    """set_setting inserts a new key-value pair."""
    await set_setting("test_key", "test_value")
    result = await get_setting("test_key")
    assert result == "test_value"


@pytest.mark.asyncio()
async def test_set_setting_overwrites_existing(db: None) -> None:
    """set_setting overwrites the value of an existing key."""
    await set_setting("overwrite_key", "first")
    await set_setting("overwrite_key", "second")
    result = await get_setting("overwrite_key")
    assert result == "second"


@pytest.mark.asyncio()
async def test_set_setting_then_get_roundtrip(db: None) -> None:
    """A set followed by a get returns the exact same value."""
    value = "complex value with spaces & symbols!"
    await set_setting("roundtrip", value)
    result = await get_setting("roundtrip")
    assert result == value


# ---------------------------------------------------------------------------
# Concurrent get_db calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_concurrent_get_db_calls_succeed(db: None) -> None:
    """Multiple concurrent get_db contexts do not deadlock or fail."""

    async def _write_setting(key: str, value: str) -> None:
        await set_setting(key, value)

    await asyncio.gather(
        _write_setting("concurrent_a", "val_a"),
        _write_setting("concurrent_b", "val_b"),
        _write_setting("concurrent_c", "val_c"),
    )

    assert await get_setting("concurrent_a") == "val_a"
    assert await get_setting("concurrent_b") == "val_b"
    assert await get_setting("concurrent_c") == "val_c"


# ---------------------------------------------------------------------------
# Composite indexes (issue #586)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_search_log_composite_indexes_present(db: None) -> None:
    """Both new indexes are created on a fresh schema.

    ``idx_search_log_lookup`` makes the v14 cooldown back-fill fast and
    serves the cooldown N+1 in :mod:`houndarr.services.metrics`;
    ``idx_search_log_action_time`` covers the action-filtered window
    aggregations on the dashboard hot path.  A regression that drops
    either trips this test before it ships.
    """
    async with get_db() as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='search_log'"
        ) as cur:
            indexes = {row["name"] async for row in cur}

    assert "idx_search_log_lookup" in indexes
    assert "idx_search_log_action_time" in indexes
    assert "idx_search_log_timestamp" in indexes
    assert "idx_search_log_instance" in indexes


@pytest.mark.asyncio()
async def test_dashboard_metric_queries_use_indexes(db: None) -> None:
    """Every metric query the dashboard polls hits an index, not a SCAN.

    Locks ``idx_search_log_lookup`` and ``idx_search_log_action_time``
    against future planner regressions.  A query that switches to a
    full ``SCAN search_log`` would re-introduce the dashboard hang
    that issue #586 fixed.
    """
    queries = [
        # Window aggregation filtered by instance + action.
        (
            "SELECT SUM(CASE WHEN action='searched' THEN 1 ELSE 0 END)"
            " FROM search_log WHERE instance_id IN (1, 2)"
        ),
        # Cooldown N+1 lookup.
        (
            "SELECT item_label FROM search_log"
            " WHERE instance_id=1 AND item_id=42 AND item_type='episode'"
            " AND action='searched' ORDER BY timestamp DESC LIMIT 1"
        ),
        # Recent searches windowed by time.
        (
            "SELECT timestamp FROM search_log"
            " WHERE action='searched' ORDER BY timestamp DESC LIMIT 5"
        ),
    ]
    async with get_db() as conn:
        for sql in queries:
            async with conn.execute(f"EXPLAIN QUERY PLAN {sql}") as cur:
                rows = await cur.fetchall()
            # Inspect every step that touches search_log.  SQLite reports
            # ``SEARCH search_log USING INDEX X`` for filtered lookups
            # and ``SCAN search_log USING INDEX X`` for full-index walks
            # (still optimal for ORDER BY + LIMIT on an indexed column);
            # the pathological case is a bare ``SCAN search_log`` with
            # no index qualifier.  We assert per-row rather than
            # joining the plan into one string so that a hypothetical
            # planner change that scans search_log via the rowid (no
            # USING INDEX clause) cannot pass on the strength of an
            # unrelated index hit elsewhere in the plan.
            for row in rows:
                detail = str(row["detail"])
                if "search_log" not in detail:
                    continue
                assert "USING INDEX" in detail or "USING COVERING INDEX" in detail, (
                    f"search_log step missing index for query: {sql}\n"
                    f"Offending step: {detail}\n"
                    f"Full plan: {[str(r['detail']) for r in rows]}"
                )


@pytest.mark.asyncio()
async def test_v14_backfill_indexed_is_fast(db: None) -> None:
    """v14 cooldown back-fill completes in seconds on a 5k-row search_log.

    Without ``idx_search_log_lookup`` the same workload runs O(N*M):
    each cooldown row triggers two full scans of ``search_log``.  This
    test seeds 500 cooldowns and 5k log rows, runs the v14 self-heal,
    and asserts the wall-clock budget.  A regression that drops the
    index would push this past 5+ s on CI.
    """
    from houndarr.database import _migrate_to_v14

    async with get_db() as conn:
        # Seed two instances; every search_log row needs a parent FK.
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            [(1, "Sonarr", "sonarr", "http://x", ""), (2, "Radarr", "radarr", "http://y", "")],
        )
        # 5k synthetic search_log rows spread over the last 30 days.
        now = datetime.now(UTC)
        log_rows = [
            (
                (i % 2) + 1,
                100 + (i % 250),  # 250 distinct items per instance
                "episode" if i % 2 == 0 else "movie",
                "missing" if i % 3 == 0 else ("cutoff" if i % 3 == 1 else "upgrade"),
                "searched",
                f"Item {i}",
                (now - timedelta(minutes=i % 43200)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            )
            for i in range(5000)
        ]
        await conn.executemany(
            "INSERT INTO search_log (instance_id, item_id, item_type, search_kind,"
            " action, item_label, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            log_rows,
        )
        # 1k cooldown rows referencing items that exist in search_log.
        cooldown_rows = [
            (
                (i % 2) + 1,
                100 + (i % 250),
                "episode" if i % 2 == 0 else "movie",
                "missing",
                now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            )
            for i in range(500)
        ]
        await conn.executemany(
            "INSERT OR IGNORE INTO cooldowns (instance_id, item_id, item_type,"
            " search_kind, searched_at) VALUES (?, ?, ?, ?, ?)",
            cooldown_rows,
        )
        await conn.commit()

    # Run the v14 self-heal twice; second run should be a near-instant
    # no-op thanks to the search_kind = 'missing' guard.
    async with get_db() as conn:
        first_start = time.monotonic()
        await _migrate_to_v14(conn)
        await conn.commit()
        first_elapsed = time.monotonic() - first_start

    async with get_db() as conn:
        second_start = time.monotonic()
        await _migrate_to_v14(conn)
        await conn.commit()
        second_elapsed = time.monotonic() - second_start

    assert first_elapsed < 5.0, f"First v14 back-fill too slow: {first_elapsed:.2f}s"
    assert second_elapsed < 0.5, f"Second v14 self-heal not idempotent-fast: {second_elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Connection pool (issue #586)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_pool_factory_applies_full_pragma_stack(db: None) -> None:
    """A connection borrowed from the pool carries every operational PRAGMA."""
    async with get_db() as conn:
        async with conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == "wal"

        async with conn.execute("PRAGMA synchronous") as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 1  # NORMAL

        async with conn.execute("PRAGMA temp_store") as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 2  # MEMORY

        async with conn.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 1


@pytest.mark.asyncio()
async def test_pool_per_db_path_isolation(tmp_data_dir: str) -> None:
    """Setting a new ``_db_path`` builds a fresh pool, leaving the old one closed.

    The test fixture pattern relies on this: every test calls
    ``set_db_path`` to a unique tmp file, and the autouse teardown
    closes the previous pool.  A regression that shares one pool
    across paths would silently route writes to the wrong DB on the
    next test.
    """
    import os

    path_a = os.path.join(tmp_data_dir, "pool_a.db")
    path_b = os.path.join(tmp_data_dir, "pool_b.db")

    set_db_path(path_a)
    await init_db()
    async with get_db() as conn:
        await conn.execute("INSERT INTO settings (key, value) VALUES ('marker', 'A')")
        await conn.commit()

    pool_a = _pools.get(path_a)
    assert pool_a is not None

    set_db_path(path_b)
    await init_db()
    async with get_db() as conn:
        async with conn.execute("SELECT value FROM settings WHERE key='marker'") as cur:
            row = await cur.fetchone()
            assert row is None  # path_b has its own DB

    pool_b = _pools.get(path_b)
    assert pool_b is not None
    assert pool_a is not pool_b

    await close_all_pools()
    assert path_a not in _pools
    assert path_b not in _pools


@pytest.mark.asyncio()
async def test_connection_factory_returns_row_factory_connection(tmp_data_dir: str) -> None:
    """``_connection_factory`` builds a connection ready for dict-style row access."""
    import os

    path = os.path.join(tmp_data_dir, "factory.db")
    set_db_path(path)
    conn = await _connection_factory()
    try:
        async with conn.execute("SELECT 1 AS one") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["one"] == 1
    finally:
        await conn.close()

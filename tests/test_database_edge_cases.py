"""Tests for database edge cases: purge_old_logs, get_setting, set_setting, concurrent access."""

from __future__ import annotations

import asyncio

import pytest

from houndarr.database import get_db
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

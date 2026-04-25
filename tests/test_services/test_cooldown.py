"""Tests for the cooldown service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.services import cooldown as cooldown_module
from houndarr.services.cooldown import (
    _reset_info_log_cache,
    _reset_skip_log_cache,
    clear_cooldowns,
    is_on_cooldown,
    record_search,
    should_log_info,
    should_log_skip,
)


async def _count_cooldowns(instance_id: int) -> int:
    """Count cooldown rows for *instance_id* directly."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE instance_id = ?",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Insert two stub instance rows so FK constraints are satisfied."""
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
                (2, "Radarr Test", "radarr", "http://radarr:7878"),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# is_on_cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_not_on_cooldown_initially(seeded_instances: None) -> None:
    result = await is_on_cooldown(1, 101, "episode", cooldown_days=7)
    assert result is False


@pytest.mark.asyncio()
async def test_on_cooldown_after_record_search(seeded_instances: None) -> None:
    await record_search(1, 101, "episode")
    result = await is_on_cooldown(1, 101, "episode", cooldown_days=7)
    assert result is True


@pytest.mark.asyncio()
async def test_cooldown_zero_days_always_false(seeded_instances: None) -> None:
    """cooldown_days=0 disables cooldowns entirely."""
    await record_search(1, 101, "episode")
    result = await is_on_cooldown(1, 101, "episode", cooldown_days=0)
    assert result is False


@pytest.mark.asyncio()
async def test_cooldown_expires_after_cooldown_days(seeded_instances: None) -> None:
    """An old search record should NOT block a new search."""
    # Record a search that happened 8 days ago
    old_time = datetime.now(UTC) - timedelta(days=8)
    old_iso = old_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns"
            " (instance_id, item_id, item_type, searched_at) VALUES (?, ?, ?, ?)",
            (1, 101, "episode", old_iso),
        )
        await conn.commit()

    result = await is_on_cooldown(1, 101, "episode", cooldown_days=7)
    assert result is False


@pytest.mark.asyncio()
async def test_cooldown_different_instances_independent(seeded_instances: None) -> None:
    """Cooldown for instance 1 should not affect instance 2."""
    await record_search(1, 101, "episode")
    result = await is_on_cooldown(2, 101, "episode", cooldown_days=7)
    assert result is False


@pytest.mark.asyncio()
async def test_cooldown_different_item_types_independent(seeded_instances: None) -> None:
    """A movie cooldown should not block an episode with the same numeric ID."""
    await record_search(1, 101, "movie")
    result = await is_on_cooldown(1, 101, "episode", cooldown_days=7)
    assert result is False


# ---------------------------------------------------------------------------
# record_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_record_search_upserts(seeded_instances: None) -> None:
    """Calling record_search twice should not create duplicate rows."""
    await record_search(1, 101, "episode")
    await record_search(1, 101, "episode")

    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns"
            " WHERE instance_id=1 AND item_id=101 AND item_type='episode'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio()
async def test_record_search_updates_timestamp(seeded_instances: None) -> None:
    """Second record_search should update searched_at to a newer value."""
    # Insert old timestamp manually
    old_time = datetime.now(UTC) - timedelta(hours=2)
    old_iso = old_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at) VALUES (?,?,?,?)",
            (1, 200, "movie", old_iso),
        )
        await conn.commit()

    await record_search(1, 200, "movie")

    async with get_db() as conn:
        async with conn.execute(
            "SELECT searched_at FROM cooldowns WHERE instance_id=1 AND item_id=200"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["searched_at"] > old_iso


# ---------------------------------------------------------------------------
# clear_cooldowns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_clear_cooldowns_removes_all(seeded_instances: None) -> None:
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")
    deleted = await clear_cooldowns(1)
    assert deleted == 2
    assert await _count_cooldowns(1) == 0


@pytest.mark.asyncio()
async def test_clear_cooldowns_only_affects_given_instance(seeded_instances: None) -> None:
    await record_search(1, 101, "episode")
    await record_search(2, 201, "movie")
    await clear_cooldowns(1)
    assert await _count_cooldowns(1) == 0
    assert await _count_cooldowns(2) == 1


@pytest.mark.asyncio()
async def test_clear_cooldowns_nonexistent_instance(seeded_instances: None) -> None:
    deleted = await clear_cooldowns(9999)
    assert deleted == 0


# ---------------------------------------------------------------------------
# should_log_skip sentinel
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sentinel() -> Iterator[None]:
    """Clear the module-level skip-log cache between tests."""
    _reset_skip_log_cache()
    yield
    _reset_skip_log_cache()


@pytest.mark.asyncio()
async def test_should_log_skip_first_call_returns_true() -> None:
    key = (1, 101, "missing", "cooldown")
    assert await should_log_skip(key) is True


@pytest.mark.asyncio()
async def test_should_log_skip_within_ttl_returns_false() -> None:
    key = (1, 101, "missing", "cooldown")
    assert await should_log_skip(key) is True
    assert await should_log_skip(key) is False
    assert await should_log_skip(key) is False


@pytest.mark.asyncio()
async def test_should_log_skip_after_ttl_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = (1, 101, "missing", "cooldown")
    assert await should_log_skip(key) is True

    # Rewind the stored timestamp by 25 hours to simulate TTL expiry.
    stale = datetime.now(UTC) - timedelta(hours=25)
    cooldown_module._SKIP_LOG_CACHE[key] = stale

    assert await should_log_skip(key) is True


@pytest.mark.asyncio()
async def test_should_log_skip_distinct_keys_independent() -> None:
    k_missing = (1, 101, "missing", "cooldown")
    k_cutoff = (1, 101, "cutoff", "cutoff_cd")
    k_upgrade = (1, 101, "upgrade", "upgrade_cd")
    k_other_item = (1, 102, "missing", "cooldown")
    k_other_instance = (2, 101, "missing", "cooldown")

    assert await should_log_skip(k_missing) is True
    assert await should_log_skip(k_cutoff) is True
    assert await should_log_skip(k_upgrade) is True
    assert await should_log_skip(k_other_item) is True
    assert await should_log_skip(k_other_instance) is True

    # Repeats of each key are suppressed independently.
    assert await should_log_skip(k_missing) is False
    assert await should_log_skip(k_cutoff) is False
    assert await should_log_skip(k_upgrade) is False


@pytest.mark.asyncio()
async def test_should_log_skip_lru_evicts_oldest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the cache hits its hard cap, the oldest entry is evicted."""
    monkeypatch.setattr(cooldown_module, "_SKIP_LOG_MAX_ENTRIES", 3)

    keys = [(1, i, "missing", "cooldown") for i in range(4)]
    for key in keys[:3]:
        assert await should_log_skip(key) is True

    # Fourth insert evicts the oldest (first) entry.
    assert await should_log_skip(keys[3]) is True
    assert len(cooldown_module._SKIP_LOG_CACHE) == 3
    assert keys[0] not in cooldown_module._SKIP_LOG_CACHE
    # Remaining three stay cached, so should_log_skip returns False for them.
    assert await should_log_skip(keys[1]) is False
    assert await should_log_skip(keys[2]) is False
    assert await should_log_skip(keys[3]) is False


@pytest.mark.asyncio()
async def test_should_log_skip_concurrent_calls_serialize() -> None:
    """Ten concurrent callers with the same key produce exactly one True."""
    key = (1, 101, "missing", "cooldown")
    results = await asyncio.gather(*(should_log_skip(key) for _ in range(10)))
    assert sum(results) == 1
    assert results.count(False) == 9


# ---------------------------------------------------------------------------
# should_log_info sentinel (caller-supplied TTL per key category)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_info_sentinel() -> Iterator[None]:
    _reset_info_log_cache()
    yield
    _reset_info_log_cache()


@pytest.mark.asyncio()
async def test_should_log_info_first_call_returns_true() -> None:
    assert await should_log_info((1, "upgrade_pool_empty"), 3600) is True


@pytest.mark.asyncio()
async def test_should_log_info_within_ttl_returns_false() -> None:
    key = (1, "upgrade_pool_empty")
    assert await should_log_info(key, 3600) is True
    assert await should_log_info(key, 3600) is False
    assert await should_log_info(key, 3600) is False


@pytest.mark.asyncio()
async def test_should_log_info_after_ttl_returns_true() -> None:
    key = (1, "upgrade_pool_empty")
    assert await should_log_info(key, 3600) is True
    # Rewind the stored timestamp past the 1h TTL.
    stale = datetime.now(UTC) - timedelta(seconds=3601)
    cooldown_module._INFO_LOG_CACHE[key] = stale
    assert await should_log_info(key, 3600) is True


@pytest.mark.asyncio()
async def test_should_log_info_distinct_instances_independent() -> None:
    k_a = (1, "upgrade_pool_empty")
    k_b = (2, "upgrade_pool_empty")
    assert await should_log_info(k_a, 3600) is True
    assert await should_log_info(k_b, 3600) is True
    # Each instance's entry is throttled independently.
    assert await should_log_info(k_a, 3600) is False
    assert await should_log_info(k_b, 3600) is False


@pytest.mark.asyncio()
async def test_should_log_info_distinct_reason_keys_independent() -> None:
    k_one = (1, "upgrade_pool_empty")
    k_two = (1, "some_other_info")
    assert await should_log_info(k_one, 3600) is True
    assert await should_log_info(k_two, 3600) is True
    assert await should_log_info(k_one, 3600) is False
    assert await should_log_info(k_two, 3600) is False


@pytest.mark.asyncio()
async def test_should_log_info_caller_owns_ttl() -> None:
    """Different callers can pass different TTLs on the same key; window of the last call wins."""
    key = (1, "upgrade_pool_empty")
    assert await should_log_info(key, 10) is True
    # Inside the 10s window, subsequent calls are suppressed regardless of TTL.
    assert await should_log_info(key, 60 * 60) is False
    assert await should_log_info(key, 1) is False


@pytest.mark.asyncio()
async def test_should_log_info_concurrent_calls_serialize() -> None:
    key = (1, "upgrade_pool_empty")
    results = await asyncio.gather(*(should_log_info(key, 3600) for _ in range(10)))
    assert sum(results) == 1
    assert results.count(False) == 9

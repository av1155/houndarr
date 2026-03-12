"""Tests for the cooldown service."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.services.cooldown import (
    clear_cooldowns,
    count_searches_last_hour,
    is_on_cooldown,
    record_search,
)


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
# count_searches_last_hour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_count_searches_zero_initially(seeded_instances: None) -> None:
    count = await count_searches_last_hour(1)
    assert count == 0


@pytest.mark.asyncio()
async def test_count_searches_after_records(seeded_instances: None) -> None:
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")
    await record_search(1, 103, "episode")
    count = await count_searches_last_hour(1)
    assert count == 3


@pytest.mark.asyncio()
async def test_count_searches_excludes_old_records(seeded_instances: None) -> None:
    """Records older than 1 hour should not be counted."""
    old_time = datetime.now(UTC) - timedelta(hours=2)
    old_iso = old_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at) VALUES (?,?,?,?)",
            (1, 999, "episode", old_iso),
        )
        await conn.commit()

    count = await count_searches_last_hour(1)
    assert count == 0


@pytest.mark.asyncio()
async def test_count_searches_per_instance(seeded_instances: None) -> None:
    """Counts for instance 1 and 2 are independent."""
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")
    await record_search(2, 201, "movie")
    assert await count_searches_last_hour(1) == 2
    assert await count_searches_last_hour(2) == 1


# ---------------------------------------------------------------------------
# clear_cooldowns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_clear_cooldowns_removes_all(seeded_instances: None) -> None:
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")
    deleted = await clear_cooldowns(1)
    assert deleted == 2
    assert await count_searches_last_hour(1) == 0


@pytest.mark.asyncio()
async def test_clear_cooldowns_only_affects_given_instance(seeded_instances: None) -> None:
    await record_search(1, 101, "episode")
    await record_search(2, 201, "movie")
    await clear_cooldowns(1)
    assert await count_searches_last_hour(1) == 0
    assert await count_searches_last_hour(2) == 1


@pytest.mark.asyncio()
async def test_clear_cooldowns_nonexistent_instance(seeded_instances: None) -> None:
    deleted = await clear_cooldowns(9999)
    assert deleted == 0

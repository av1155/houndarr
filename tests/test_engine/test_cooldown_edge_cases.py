"""Tests for cooldown boundary conditions, synthetic IDs, and item_type independence."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.engine.adapters.lidarr import _artist_item_id
from houndarr.engine.adapters.readarr import _author_item_id
from houndarr.engine.adapters.sonarr import _season_item_id
from houndarr.engine.adapters.whisparr_v2 import (
    _season_item_id as whisparr_v2_season_item_id,
)
from houndarr.repositories.cooldowns import _iso
from houndarr.services.cooldown import (
    clear_cooldowns,
    is_on_cooldown,
    record_search,
)


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Insert two stub instance rows so FK constraints are satisfied."""
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "Sonarr", "sonarr", "http://sonarr:8989", "enc"),
                (2, "Radarr", "radarr", "http://radarr:7878", "enc"),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Boundary: exact expiry point (strict > means NOT on cooldown)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cooldown_exact_boundary_not_on_cooldown(
    seeded_instances: None,
) -> None:
    """When searched_at + cooldown_days == now, the item is NOT on cooldown.

    The query uses ``searched_at > cutoff`` (strict greater-than), so when
    searched_at equals the cutoff exactly, it does not match.
    """
    cooldown_days = 7
    boundary = datetime.now(UTC) - timedelta(days=cooldown_days)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (1, 101, "episode", _iso(boundary)),
        )
        await conn.commit()

    result = await is_on_cooldown(1, 101, "episode", cooldown_days=cooldown_days)
    assert result is False


@pytest.mark.asyncio()
async def test_cooldown_one_second_before_boundary_on_cooldown(
    seeded_instances: None,
) -> None:
    """One second after the cutoff means searched_at > cutoff: still on cooldown."""
    cooldown_days = 7
    just_inside = datetime.now(UTC) - timedelta(days=cooldown_days) + timedelta(seconds=1)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (1, 101, "episode", _iso(just_inside)),
        )
        await conn.commit()

    result = await is_on_cooldown(1, 101, "episode", cooldown_days=cooldown_days)
    assert result is True


# ---------------------------------------------------------------------------
# Zero / negative cooldown_days
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_zero_cooldown_days_always_false(seeded_instances: None) -> None:
    """Passing cooldown_days=0 disables cooldowns; always returns False."""
    await record_search(1, 101, "episode")
    result = await is_on_cooldown(1, 101, "episode", cooldown_days=0)
    assert result is False


@pytest.mark.asyncio()
async def test_negative_cooldown_days_always_false(seeded_instances: None) -> None:
    """Negative cooldown_days also disables cooldowns."""
    await record_search(1, 101, "episode")
    result = await is_on_cooldown(1, 101, "episode", cooldown_days=-5)
    assert result is False


# ---------------------------------------------------------------------------
# Synthetic ID formula tests (pure, no DB)
# ---------------------------------------------------------------------------


def test_sonarr_season_synthetic_id_format() -> None:
    assert _season_item_id(55, 3) == -(55 * 1000 + 3)
    assert _season_item_id(55, 3) == -55003


def test_sonarr_season_synthetic_id_season_zero() -> None:
    """Specials (season 0) produce a valid synthetic ID."""
    assert _season_item_id(55, 0) == -55000


def test_lidarr_artist_synthetic_id_format() -> None:
    assert _artist_item_id(50) == -(50 * 1000)
    assert _artist_item_id(50) == -50000


def test_readarr_author_synthetic_id_format() -> None:
    assert _author_item_id(60) == -(60 * 1000)
    assert _author_item_id(60) == -60000


def test_whisparr_v2_season_synthetic_id_matches_formula() -> None:
    """Whisparr v2 uses the same formula as Sonarr."""
    assert whisparr_v2_season_item_id(70, 2) == -(70 * 1000 + 2)
    assert whisparr_v2_season_item_id(70, 2) == -70002


# ---------------------------------------------------------------------------
# item_type independence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cooldown_episode_vs_movie_independent(
    seeded_instances: None,
) -> None:
    """A cooldown for item_type=episode does not affect item_type=movie."""
    await record_search(1, 101, "episode")
    on_cd = await is_on_cooldown(1, 101, "movie", cooldown_days=7)
    assert on_cd is False


@pytest.mark.asyncio()
async def test_cooldown_episode_vs_whisparr_v2_episode_independent(
    seeded_instances: None,
) -> None:
    """episode and whisparr_v2_episode are tracked independently."""
    await record_search(1, 101, "episode")
    on_cd = await is_on_cooldown(1, 101, "whisparr_v2_episode", cooldown_days=7)
    assert on_cd is False


@pytest.mark.asyncio()
async def test_cooldown_album_vs_book_independent(
    seeded_instances: None,
) -> None:
    """album and book are tracked independently for the same item_id."""
    await record_search(1, 301, "album")
    on_cd = await is_on_cooldown(1, 301, "book", cooldown_days=7)
    assert on_cd is False


# ---------------------------------------------------------------------------
# Synthetic negative IDs in DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cooldown_synthetic_negative_id_tracked(
    seeded_instances: None,
) -> None:
    """Negative synthetic IDs (e.g. season-level) are stored and looked up correctly."""
    synthetic_id = _season_item_id(55, 3)
    assert synthetic_id < 0

    await record_search(1, synthetic_id, "episode")
    on_cd = await is_on_cooldown(1, synthetic_id, "episode", cooldown_days=7)
    assert on_cd is True


# ---------------------------------------------------------------------------
# Different cooldown durations on same data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cooldown_14d_vs_21d_different_behavior(
    seeded_instances: None,
) -> None:
    """A search 15 days ago is expired for 14-day cooldown but active for 21-day."""
    fifteen_days_ago = datetime.now(UTC) - timedelta(days=15)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (1, 200, "movie", _iso(fifteen_days_ago)),
        )
        await conn.commit()

    expired_14 = await is_on_cooldown(1, 200, "movie", cooldown_days=14)
    assert expired_14 is False

    active_21 = await is_on_cooldown(1, 200, "movie", cooldown_days=21)
    assert active_21 is True


# ---------------------------------------------------------------------------
# Upsert behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cooldown_upsert_updates_timestamp(
    seeded_instances: None,
) -> None:
    """A second record_search updates the timestamp, not duplicates the row."""
    await record_search(1, 101, "episode")
    await record_search(1, 101, "episode")

    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE instance_id=1 AND item_id=101"
            " AND item_type='episode'",
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert int(row[0]) == 1


# ---------------------------------------------------------------------------
# Per-instance isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cooldown_per_instance_isolation(
    seeded_instances: None,
) -> None:
    """A cooldown on instance 1 does not affect instance 2."""
    await record_search(1, 101, "episode")
    on_cd = await is_on_cooldown(2, 101, "episode", cooldown_days=7)
    assert on_cd is False


# ---------------------------------------------------------------------------
# clear_cooldowns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_clear_cooldowns_only_affects_target_instance(
    seeded_instances: None,
) -> None:
    """Clearing cooldowns for instance 1 does not remove instance 2 records."""
    await record_search(1, 101, "episode")
    await record_search(2, 201, "movie")

    await clear_cooldowns(1)

    on_cd_1 = await is_on_cooldown(1, 101, "episode", cooldown_days=7)
    on_cd_2 = await is_on_cooldown(2, 201, "movie", cooldown_days=7)
    assert on_cd_1 is False
    assert on_cd_2 is True


@pytest.mark.asyncio()
async def test_clear_cooldowns_returns_deleted_count(
    seeded_instances: None,
) -> None:
    """clear_cooldowns returns the number of rows deleted."""
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")
    await record_search(1, 103, "movie")

    deleted = await clear_cooldowns(1)
    assert deleted == 3


@pytest.mark.asyncio()
async def test_clear_cooldowns_nonexistent_returns_zero(
    seeded_instances: None,
) -> None:
    """Clearing cooldowns for an instance with none returns 0."""
    deleted = await clear_cooldowns(1)
    assert deleted == 0

"""Tests for _run_upgrade_pass() hard caps, rotation, and offset persistence."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.cooldown import record_search
from houndarr.services.instances import InstanceType

from .conftest import (
    _COMMAND_RESP,
    MASTER_KEY,
    RADARR_URL,
    SONARR_URL,
    WHISPARR_URL,
    get_log_rows,
    make_instance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_PAGE: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 0,
    "records": [],
}


def _radarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 2,
        "itype": InstanceType.radarr,
        "batch_size": 0,
        "hourly_cap": 0,
        "cooldown_days": 7,
        "upgrade_enabled": True,
        "upgrade_batch_size": 3,
        "upgrade_hourly_cap": 5,
        "upgrade_cooldown_days": 90,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 0,
        "hourly_cap": 0,
        "cooldown_days": 7,
        "upgrade_enabled": True,
        "upgrade_batch_size": 3,
        "upgrade_hourly_cap": 5,
        "upgrade_cooldown_days": 90,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _whisparr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 5,
        "itype": InstanceType.whisparr_v2,
        "batch_size": 0,
        "hourly_cap": 0,
        "cooldown_days": 7,
        "upgrade_enabled": True,
        "upgrade_batch_size": 3,
        "upgrade_hourly_cap": 5,
        "upgrade_cooldown_days": 90,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _lidarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 3,
        "itype": InstanceType.lidarr,
        "batch_size": 0,
        "hourly_cap": 0,
        "cooldown_days": 7,
        "upgrade_enabled": True,
        "upgrade_batch_size": 3,
        "upgrade_hourly_cap": 5,
        "upgrade_cooldown_days": 90,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _readarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 4,
        "itype": InstanceType.readarr,
        "batch_size": 0,
        "hourly_cap": 0,
        "cooldown_days": 7,
        "upgrade_enabled": True,
        "upgrade_batch_size": 3,
        "upgrade_hourly_cap": 5,
        "upgrade_cooldown_days": 90,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _library_movie(movie_id: int, cutoff_met: bool = True) -> dict[str, Any]:
    """Radarr library movie record eligible for upgrade."""
    return {
        "id": movie_id,
        "title": f"Movie {movie_id}",
        "year": 2023,
        "monitored": True,
        "hasFile": True,
        "movieFile": {"qualityCutoffNotMet": not cutoff_met},
        "inCinemas": "2023-01-01T00:00:00Z",
        "physicalRelease": None,
        "digitalRelease": None,
    }


def _mock_radarr_empty_missing() -> None:
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )


def _mock_radarr_library(movies: list[dict[str, Any]]) -> None:
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=movies),
    )


def _mock_radarr_command() -> None:
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


# ---------------------------------------------------------------------------
# Batch size hard cap tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_batch_size_clamped_to_5(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_batch_size=10 is clamped to hard cap of 5."""
    movies = [_library_movie(200 + i) for i in range(8)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(upgrade_batch_size=10, upgrade_hourly_cap=5)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 5


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_batch_size_3_not_clamped(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_batch_size=3 is under hard cap; 3 items searched."""
    movies = [_library_movie(200 + i) for i in range(5)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(upgrade_batch_size=3, upgrade_hourly_cap=5)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 3


# ---------------------------------------------------------------------------
# Hourly cap hard cap tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_hourly_cap_clamped_to_5(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_hourly_cap=10 is clamped to 5."""
    movies = [_library_movie(200 + i) for i in range(8)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_batch_size=5,
        upgrade_hourly_cap=10,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 5


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_hourly_cap_1_not_clamped(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_hourly_cap=1: only 1 item searched despite 3 available."""
    movies = [_library_movie(200 + i) for i in range(3)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_batch_size=5,
        upgrade_hourly_cap=1,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 1


# ---------------------------------------------------------------------------
# Cooldown days min clamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_cooldown_days_clamped_to_min_7(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_cooldown_days=3 is clamped to minimum 7."""
    from datetime import UTC, datetime, timedelta

    from houndarr.database import get_db

    # Record a search 4 days ago (within 7-day window, outside 3-day window)
    four_days_ago = (datetime.now(UTC) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    movies = [_library_movie(200)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    # Manually insert cooldown 4 days old
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (2, 200, "movie", four_days_ago),
        )
        await conn.commit()

    inst = _radarr_instance(upgrade_cooldown_days=3)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    # Clamped to 7 days, so 4-day-old cooldown still blocks
    assert len(upgrade_searched) == 0


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_cooldown_days_90_not_clamped(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_cooldown_days=90 stays at 90 (above min)."""
    from datetime import UTC, datetime, timedelta

    from houndarr.database import get_db

    # Record a search 10 days ago (within 90-day window)
    ten_days_ago = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    movies = [_library_movie(200)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (2, 200, "movie", ten_days_ago),
        )
        await conn.commit()

    inst = _radarr_instance(upgrade_cooldown_days=90)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    # 10 days < 90 day cooldown, so still blocked
    assert len(upgrade_searched) == 0


# ---------------------------------------------------------------------------
# Zero batch size
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_zero_batch_size_returns_zero(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """upgrade_batch_size=0 returns 0 immediately."""
    _mock_radarr_empty_missing()
    _mock_radarr_library([_library_movie(200)])
    _mock_radarr_command()

    inst = _radarr_instance(upgrade_batch_size=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


# ---------------------------------------------------------------------------
# Empty pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_empty_pool_logs_info(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Empty library logs 'upgrade pool empty'."""
    _mock_radarr_empty_missing()
    _mock_radarr_library([])
    _mock_radarr_command()

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    info_rows = [r for r in rows if r["action"] == "info" and r["search_kind"] == "upgrade"]
    assert len(info_rows) == 1
    assert "upgrade pool empty" in (info_rows[0].get("message") or "")


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_empty_pool_returns_zero(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Empty library returns 0."""
    _mock_radarr_empty_missing()
    _mock_radarr_library([])
    _mock_radarr_command()

    inst = _radarr_instance()
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


# ---------------------------------------------------------------------------
# Pool fetch error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_pool_fetch_error_returns_zero(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Exception from fetch: logged, returns 0."""
    _mock_radarr_empty_missing()
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        side_effect=httpx.ConnectError("timeout"),
    )

    inst = _radarr_instance()
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    error_rows = [r for r in rows if r["action"] == "error" and r["search_kind"] == "upgrade"]
    assert len(error_rows) == 1


# ---------------------------------------------------------------------------
# Offset rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_offset_rotation(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """offset=2, pool=[200,201,202,203,204]: starts searching from 202."""
    movies = [_library_movie(200 + i) for i in range(5)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_item_offset=2,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"]
    assert len(searched) == 1
    assert searched[0]["item_id"] == 202


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_offset_wraparound(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """offset=7, pool size 5: wraps to offset 2."""
    movies = [_library_movie(200 + i) for i in range(5)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_item_offset=7,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"]
    assert len(searched) == 1
    # 7 % 5 = 2, so item 202
    assert searched[0]["item_id"] == 202


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_offset_persisted(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """update_instance is called to persist the new item offset."""
    movies = [_library_movie(200 + i) for i in range(5)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_item_offset=0,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    # update_instance should be called at least once with upgrade_item_offset
    calls_with_offset = [c for c in mock_update.call_args_list if "upgrade_item_offset" in c.kwargs]
    assert len(calls_with_offset) >= 1


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_offset_zero_no_rotation(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """offset=0: first item (lowest ID) searched first."""
    movies = [_library_movie(203), _library_movie(201), _library_movie(202)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_item_offset=0,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"]
    assert len(searched) == 1
    # Pool sorted by ID: [201, 202, 203]; offset=0 gives 201
    assert searched[0]["item_id"] == 201


# ---------------------------------------------------------------------------
# Series offset for Sonarr and Whisparr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_sonarr_series_offset_advanced_by_5(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Sonarr: update_instance called with series_offset+5."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{SONARR_URL}/api/v3/series").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 55, "monitored": True, "title": "Show"},
            ],
        ),
    )
    respx.get(f"{SONARR_URL}/api/v3/episode").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 101,
                    "seriesId": 55,
                    "title": "Ep",
                    "seasonNumber": 1,
                    "episodeNumber": 1,
                    "monitored": True,
                    "hasFile": True,
                    "episodeFile": {"qualityCutoffNotMet": False},
                    "series": {"id": 55, "title": "Show"},
                },
            ],
        ),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        upgrade_series_offset=0,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    calls_with_series = [
        c for c in mock_update.call_args_list if "upgrade_series_offset" in c.kwargs
    ]
    assert len(calls_with_series) >= 1
    assert calls_with_series[0].kwargs["upgrade_series_offset"] == 5


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_whisparr_series_offset_advanced_by_5(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Whisparr: update_instance called with series_offset+5."""
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{WHISPARR_URL}/api/v3/series").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 70, "monitored": True, "title": "Show"},
            ],
        ),
    )
    respx.get(f"{WHISPARR_URL}/api/v3/episode").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 501,
                    "seriesId": 70,
                    "title": "Scene",
                    "seasonNumber": 1,
                    "monitored": True,
                    "hasFile": True,
                    "episodeFile": {"qualityCutoffNotMet": False},
                    "series": {"id": 70, "title": "Show"},
                },
            ],
        ),
    )
    respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _whisparr_instance(
        upgrade_series_offset=0,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    calls_with_series = [
        c for c in mock_update.call_args_list if "upgrade_series_offset" in c.kwargs
    ]
    assert len(calls_with_series) >= 1
    assert calls_with_series[0].kwargs["upgrade_series_offset"] == 5


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_radarr_series_offset_not_advanced(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """No series_offset update for Radarr."""
    movies = [_library_movie(200)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(upgrade_batch_size=1, upgrade_hourly_cap=5)
    await run_instance_search(inst, MASTER_KEY)

    calls_with_series = [
        c for c in mock_update.call_args_list if "upgrade_series_offset" in c.kwargs
    ]
    assert len(calls_with_series) == 0


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_lidarr_series_offset_not_advanced(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """No series_offset update for Lidarr."""
    from .conftest import LIDARR_URL

    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    # Lidarr upgrade pool fetches cutoff exclusion then library
    respx.get(f"{LIDARR_URL}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{LIDARR_URL}/api/v1/album").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 301,
                    "artistId": 50,
                    "title": "Album",
                    "monitored": True,
                    "statistics": {"trackFileCount": 1},
                    "releaseDate": "2023-03-15T00:00:00Z",
                    "artist": {"id": 50, "artistName": "Artist"},
                },
            ],
        ),
    )
    respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _lidarr_instance(upgrade_batch_size=1, upgrade_hourly_cap=5)
    await run_instance_search(inst, MASTER_KEY)

    calls_with_series = [
        c for c in mock_update.call_args_list if "upgrade_series_offset" in c.kwargs
    ]
    assert len(calls_with_series) == 0


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_readarr_series_offset_not_advanced(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """No series_offset update for Readarr."""
    from .conftest import READARR_URL

    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{READARR_URL}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{READARR_URL}/api/v1/book").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 401,
                    "authorId": 60,
                    "title": "Book",
                    "monitored": True,
                    "statistics": {"bookFileCount": 1},
                    "releaseDate": "2023-06-01T00:00:00Z",
                    "author": {"id": 60, "authorName": "Author"},
                },
            ],
        ),
    )
    respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _readarr_instance(upgrade_batch_size=1, upgrade_hourly_cap=5)
    await run_instance_search(inst, MASTER_KEY)

    calls_with_series = [
        c for c in mock_update.call_args_list if "upgrade_series_offset" in c.kwargs
    ]
    assert len(calls_with_series) == 0


# ---------------------------------------------------------------------------
# Hourly cap blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_hourly_cap_blocks(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """3 items, cap=2: 2 searched, 1 skipped."""
    movies = [_library_movie(200 + i) for i in range(3)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_batch_size=5,
        upgrade_hourly_cap=2,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    upgrade_skipped = [
        r for r in rows if r["action"] == "skipped" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 2
    assert len(upgrade_skipped) >= 1


# ---------------------------------------------------------------------------
# Cooldown blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_cooldown_blocks_item(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Item on cooldown is skipped in upgrade pass."""
    await record_search(2, 200, "movie")

    movies = [_library_movie(200)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
        upgrade_cooldown_days=90,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 0


# ---------------------------------------------------------------------------
# Dispatch error continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_dispatch_error_continues(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Error on item 1, item 2 still searched."""
    movies = [_library_movie(200), _library_movie(201)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        side_effect=[
            httpx.Response(500, text="error"),
            httpx.Response(201, json=_COMMAND_RESP),
        ],
    )

    inst = _radarr_instance(
        upgrade_batch_size=5,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"]
    errors = [r for r in rows if r["action"] == "error" and r["search_kind"] == "upgrade"]
    assert len(searched) == 1
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Pool sort order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_pool_sorted_by_item_id(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Items arrive unsorted; searched in sorted (by ID) order."""
    movies = [
        _library_movie(205),
        _library_movie(201),
        _library_movie(203),
    ]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_item_offset=0,
        upgrade_batch_size=3,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"]
    searched_ids = [r["item_id"] for r in searched]
    assert searched_ids == [201, 203, 205]


# ---------------------------------------------------------------------------
# Offset advances on cooldown skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_offset_advances_on_cooldown_skip(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Offset moves even for skipped (cooldown) items."""
    await record_search(2, 200, "movie")

    # Use 3 movies so (offset + scanned) % 3 != 0 after processing
    movies = [_library_movie(200), _library_movie(201), _library_movie(202)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_item_offset=0,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
        upgrade_cooldown_days=90,
    )
    await run_instance_search(inst, MASTER_KEY)

    # update_instance called; offset advanced past the cooldown skip
    calls_with_offset = [c for c in mock_update.call_args_list if "upgrade_item_offset" in c.kwargs]
    assert len(calls_with_offset) >= 1
    persisted_offset = calls_with_offset[-1].kwargs["upgrade_item_offset"]
    # Skip item 200 (cooldown) + search item 201 = scanned 2 items, (0+2)%3 = 2
    assert persisted_offset == 2


# ---------------------------------------------------------------------------
# Offset persist failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
    side_effect=RuntimeError("db error"),
)
async def test_upgrade_offset_persist_failure_no_crash(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """update_instance raises: warning logged, no crash."""
    movies = [_library_movie(200)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(upgrade_batch_size=1, upgrade_hourly_cap=5)
    # Should not raise
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    assert len(upgrade_searched) == 1


# ---------------------------------------------------------------------------
# Scan budget limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_scan_budget_limits(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """scan_budget reached before batch: stops iteration."""
    # batch_size=1 => scan_budget = clamp(1*8, 8, 40) = 8
    # Put 7 items on cooldown so they consume scan budget
    for i in range(7):
        await record_search(2, 200 + i, "movie")

    movies = [_library_movie(200 + i) for i in range(10)]
    _mock_radarr_empty_missing()
    _mock_radarr_library(movies)
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
        upgrade_cooldown_days=90,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    # scan_budget=8: 7 on cooldown consume 7, then item 207 consumes 8th
    assert len(upgrade_searched) == 1

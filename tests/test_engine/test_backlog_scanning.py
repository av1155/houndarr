"""Tests for multi-page pagination and scan budget in _run_search_pass()."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from houndarr.engine.search_loop import (
    _cutoff_page_size,
    _missing_page_size,
    _missing_scan_budget,
    run_instance_search,
)
from houndarr.services.cooldown import record_search
from houndarr.services.instances import InstanceType

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    _MOVIE_RECORD,
    MASTER_KEY,
    RADARR_URL,
    SONARR_URL,
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


def _sonarr_page(
    records: list[dict[str, Any]],
    page: int = 1,
) -> dict[str, Any]:
    return {
        "page": page,
        "pageSize": len(records),
        "totalRecords": len(records),
        "records": records,
    }


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _radarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 2,
        "itype": InstanceType.radarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _make_episode(episode_id: int, series_id: int = 55) -> dict[str, Any]:
    return {
        **_EPISODE_RECORD,
        "id": episode_id,
        "seriesId": series_id,
    }


def _make_movie(movie_id: int) -> dict[str, Any]:
    return {**_MOVIE_RECORD, "id": movie_id}


# ---------------------------------------------------------------------------
# Multi-page pagination tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_page_one_all_on_cooldown_advances_to_page_two(
    seeded_instances: None,
) -> None:
    """Items on cooldown on page 1 cause page 2 to be fetched."""
    # Put page-1 items on cooldown
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")

    page1 = _sonarr_page([_make_episode(101), _make_episode(102)])
    page2 = _sonarr_page([_make_episode(103), _make_episode(104)])

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(batch_size=2)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 2
    rows = await get_log_rows()
    searched_ids = [r["item_id"] for r in rows if r["action"] == "searched"]
    assert 103 in searched_ids
    assert 104 in searched_ids


@pytest.mark.asyncio()
@respx.mock
async def test_five_pages_all_on_cooldown_ends_gracefully(
    seeded_instances: None,
) -> None:
    """5 pages of items all on cooldown returns 0 without errors."""
    for i in range(50):
        await record_search(2, 300 + i, "movie")

    pages = []
    for p in range(5):
        records = [_make_movie(300 + p * 10 + j) for j in range(10)]
        pages.append(httpx.Response(200, json=_sonarr_page(records)))

    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        side_effect=pages,
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(batch_size=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_empty_page_terminates_early(
    seeded_instances: None,
) -> None:
    """An empty page stops pagination; no further pages are fetched."""
    page1 = _sonarr_page([_make_episode(101)])
    page2: dict[str, Any] = {
        "page": 2,
        "pageSize": 10,
        "totalRecords": 0,
        "records": [],
    }
    # A third page should never be requested
    page3_route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=_sonarr_page([_make_episode(999)])),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(batch_size=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    # Only 2 GET calls (page 1 + empty page 2)
    assert page3_route.call_count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_batch_target_reached_stops_pagination(
    seeded_instances: None,
) -> None:
    """Once batch_size items are searched, pagination stops."""
    page1 = _sonarr_page(
        [
            _make_episode(101),
            _make_episode(102),
            _make_episode(103),
        ]
    )
    missing_route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=page1),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(batch_size=2)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 2
    # Only 1 page fetched since batch filled
    assert missing_route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_mixed_cooldown_and_fresh_same_page(
    seeded_instances: None,
) -> None:
    """A page with both on-cooldown and fresh items: only fresh searched."""
    await record_search(1, 101, "episode")

    page1 = _sonarr_page([_make_episode(101), _make_episode(102)])
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=page1),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(batch_size=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched"]
    assert searched[0]["item_id"] == 102


@pytest.mark.asyncio()
@respx.mock
async def test_item_dedup_across_pages(seeded_instances: None) -> None:
    """Same item_id on pages 1 and 2 is searched only once."""
    page1 = _sonarr_page([_make_episode(101)])
    page2 = _sonarr_page([_make_episode(101), _make_episode(102)])
    empty = _sonarr_page([])
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=empty),
            httpx.Response(200, json=empty),
            httpx.Response(200, json=empty),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(batch_size=5)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched"]
    searched_ids = [r["item_id"] for r in searched]
    assert searched_ids.count(101) == 1
    assert 102 in searched_ids


@pytest.mark.asyncio()
@respx.mock
async def test_context_mode_group_key_dedup_across_pages(
    seeded_instances: None,
) -> None:
    """Same (series_id, season) on pages 1 and 2 triggers one SeasonSearch."""
    from houndarr.services.instances import SonarrSearchMode

    ep1 = {**_make_episode(101, series_id=55), "seasonNumber": 1}
    ep2 = {**_make_episode(102, series_id=55), "seasonNumber": 1}

    page1 = _sonarr_page([ep1])
    page2 = _sonarr_page([ep2])
    empty = _sonarr_page([])

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=empty),
            httpx.Response(200, json=empty),
            httpx.Response(200, json=empty),
        ],
    )
    cmd_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        batch_size=5,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    assert cmd_route.call_count == 1


# ---------------------------------------------------------------------------
# Page size and scan budget pure function tests
# ---------------------------------------------------------------------------


def test_page_size_min_clamped_missing() -> None:
    """batch_size=1 yields page_size=10 (minimum)."""
    assert _missing_page_size(1) == 10


def test_page_size_max_clamped_missing() -> None:
    """batch_size=100 yields page_size=50 (maximum)."""
    assert _missing_page_size(100) == 50


def test_page_size_min_clamped_cutoff() -> None:
    """batch_size=1 yields cutoff page_size=5 (minimum)."""
    assert _cutoff_page_size(1) == 5


def test_page_size_max_clamped_cutoff() -> None:
    """batch_size=100 yields cutoff page_size=25 (maximum)."""
    assert _cutoff_page_size(100) == 25


def test_scan_budget_min_clamped() -> None:
    """batch_size=1 yields missing scan_budget=24 (minimum)."""
    assert _missing_scan_budget(1) == 24


def test_scan_budget_max_clamped() -> None:
    """batch_size=100 yields missing scan_budget=120 (maximum)."""
    assert _missing_scan_budget(100) == 120

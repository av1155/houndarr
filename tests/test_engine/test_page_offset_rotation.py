"""Tests for page-offset rotation in missing and cutoff passes."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.cooldown import record_search
from houndarr.services.instances import InstanceType, get_instance

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    _MOVIE_RECORD,
    MASTER_KEY,
    SONARR_URL,
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


def _sonarr_page(records: list[dict[str, Any]], page: int = 1) -> dict[str, Any]:
    return {
        "page": page,
        "pageSize": len(records),
        "totalRecords": len(records),
        "records": records,
    }


def _make_episode(episode_id: int, series_id: int = 55) -> dict[str, Any]:
    return {**_EPISODE_RECORD, "id": episode_id, "seriesId": series_id}


def _make_movie(movie_id: int) -> dict[str, Any]:
    return {**_MOVIE_RECORD, "id": movie_id}


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 2,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _radarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 2,
        "itype": InstanceType.radarr,
        "batch_size": 2,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


# ---------------------------------------------------------------------------
# Page-offset rotation: missing pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_missing_pass_starts_from_stored_page_offset(
    seeded_instances: None,
) -> None:
    """When missing_page_offset=2, the first fetch should request page 2."""
    page2 = _sonarr_page([_make_episode(201), _make_episode(202)])

    route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing")
    route.mock(
        side_effect=[
            httpx.Response(200, json=page2),
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(missing_page_offset=2)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 2
    # Verify the first request used page=2
    first_request = route.calls[0].request
    assert "page=2" in str(first_request.url)


@pytest.mark.asyncio()
@respx.mock
async def test_missing_pass_wraps_on_empty_page(
    seeded_instances: None,
) -> None:
    """When start_page is past available data, wraps to page 1."""
    page1 = _sonarr_page([_make_episode(101)])

    route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing")
    route.mock(
        side_effect=[
            # Page 5 (start_page) returns empty
            httpx.Response(200, json=_EMPTY_PAGE),
            # Wraps to page 1
            httpx.Response(200, json=page1),
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(missing_page_offset=5, batch_size=2)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    # First fetch was page 5, second was page 1 (wrap)
    assert "page=5" in str(route.calls[0].request.url)
    assert "page=1" in str(route.calls[1].request.url)


@pytest.mark.asyncio()
@respx.mock
async def test_missing_pass_no_double_wrap(
    seeded_instances: None,
) -> None:
    """When both the start page and page 1 are empty, the pass stops."""
    route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing")
    route.mock(
        side_effect=[
            # Page 3 empty
            httpx.Response(200, json=_EMPTY_PAGE),
            # Page 1 (wrap) also empty
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )

    inst = _sonarr_instance(missing_page_offset=3, batch_size=2)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    # Only two fetches: page 3, then page 1 (wrap), then stop
    assert len(route.calls) == 2


@pytest.mark.asyncio()
@respx.mock
async def test_missing_page_offset_persisted_after_cycle(
    seeded_instances: None,
) -> None:
    """After the missing pass, the next page offset is saved to the DB."""
    page1 = _sonarr_page([_make_episode(101)])

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(missing_page_offset=1, batch_size=2)
    await run_instance_search(inst, MASTER_KEY)

    # Verify the offset was persisted (should be page 2 after consuming page 1)
    updated = await get_instance(1, master_key=MASTER_KEY)
    assert updated is not None
    assert updated.missing_page_offset == 2


@pytest.mark.asyncio()
@respx.mock
async def test_missing_offset_advances_across_cycles(
    seeded_instances: None,
) -> None:
    """Consecutive cycles advance the page offset progressively."""
    # Each cycle: items on cooldown force the offset to advance.
    # Cycle 1: start at page 1, items on cooldown, advance
    await record_search(1, 101, "episode")
    await record_search(1, 102, "episode")

    page1 = _sonarr_page([_make_episode(101), _make_episode(102)])
    page2 = _sonarr_page([_make_episode(201)])

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(missing_page_offset=1, batch_size=1)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    updated = await get_instance(1, master_key=MASTER_KEY)
    assert updated is not None
    # The pass consumed pages 1 and 2, so next start is page 3
    assert updated.missing_page_offset == 3


# ---------------------------------------------------------------------------
# Page-offset rotation: cutoff pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_starts_from_stored_page_offset(
    seeded_instances: None,
) -> None:
    """When cutoff_page_offset=2, the cutoff fetch starts at page 2."""
    cutoff_page2 = _sonarr_page([_make_episode(301)])

    # Missing pass: empty (skip quickly)
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    cutoff_route = respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff")
    cutoff_route.mock(
        side_effect=[
            httpx.Response(200, json=cutoff_page2),
            # Page 3 empty, wrap to page 1, page 1 empty -> stop
            httpx.Response(200, json=_EMPTY_PAGE),
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        cutoff_enabled=True,
        cutoff_batch_size=2,
        cutoff_cooldown_days=7,
        cutoff_hourly_cap=0,
        cutoff_page_offset=2,
        batch_size=0,
    )
    await run_instance_search(inst, MASTER_KEY)

    # Verify the first cutoff request used page=2
    first_cutoff = cutoff_route.calls[0].request
    assert "page=2" in str(first_cutoff.url)


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_page_offset_persisted_after_cycle(
    seeded_instances: None,
) -> None:
    """After the cutoff pass, the next page offset is saved to the DB."""
    cutoff_page1 = _sonarr_page([_make_episode(301)])

    # Missing pass: empty
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        side_effect=[
            httpx.Response(200, json=cutoff_page1),
            httpx.Response(200, json=_EMPTY_PAGE),
        ],
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        cutoff_enabled=True,
        cutoff_batch_size=2,
        cutoff_cooldown_days=7,
        cutoff_hourly_cap=0,
        cutoff_page_offset=1,
        batch_size=0,
    )
    await run_instance_search(inst, MASTER_KEY)

    updated = await get_instance(1, master_key=MASTER_KEY)
    assert updated is not None
    assert updated.cutoff_page_offset == 2


# ---------------------------------------------------------------------------
# Partial page consumption: offset should not skip past the current page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_batch_fills_mid_page_does_not_skip_page(
    seeded_instances: None,
) -> None:
    """When the batch fills partway through a page, the offset stays on that page."""
    # Page 1 has 5 items, but batch_size=1 means only 1 will be searched.
    # The remaining 4 items should not be skipped by the offset advancing.
    big_page = _sonarr_page(
        [
            _make_episode(101),
            _make_episode(102),
            _make_episode(103),
            _make_episode(104),
            _make_episode(105),
        ]
    )

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=big_page),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(missing_page_offset=1, batch_size=1)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    # Offset should stay at page 1 (not advance to 2) since the page
    # was only partially consumed.
    updated = await get_instance(1, master_key=MASTER_KEY)
    assert updated is not None
    assert updated.missing_page_offset == 1


# ---------------------------------------------------------------------------
# Start page=1 (default): no wrapping attempted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_no_wrap_when_start_page_is_one(
    seeded_instances: None,
) -> None:
    """When start_page=1 and page returns empty, pass stops (no wrap)."""
    route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing")
    route.mock(return_value=httpx.Response(200, json=_EMPTY_PAGE))

    inst = _sonarr_instance(missing_page_offset=1, batch_size=2)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    # Only one fetch: page 1 empty, no wrap attempted
    assert len(route.calls) == 1

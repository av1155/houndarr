"""Tests for run_instance_search() orchestration of missing + cutoff + upgrade passes."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import InstanceType

from .conftest import (
    _COMMAND_RESP,
    _MOVIE_RECORD,
    MASTER_KEY,
    RADARR_URL,
    get_log_rows,
    make_instance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MISSING_RADARR_1: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_MOVIE_RECORD],
}
_CUTOFF_RADARR_1: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [
        {
            **_MOVIE_RECORD,
            "id": 202,
            "title": "Cutoff Movie",
        },
    ],
}
_EMPTY_PAGE: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 0,
    "records": [],
}
_LIBRARY_MOVIE_UPGRADE: list[dict[str, Any]] = [
    {
        "id": 203,
        "title": "Upgrade Movie",
        "year": 2023,
        "monitored": True,
        "hasFile": True,
        "movieFile": {"qualityCutoffNotMet": False},
        "inCinemas": "2023-01-01T00:00:00Z",
        "physicalRelease": None,
        "digitalRelease": None,
    },
]


def _mock_radarr_missing(payload: dict[str, Any] = _MISSING_RADARR_1) -> None:
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=payload),
    )


def _mock_radarr_cutoff(payload: dict[str, Any] = _CUTOFF_RADARR_1) -> None:
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=payload),
    )


def _mock_radarr_command() -> None:
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


def _mock_radarr_library(
    movies: list[dict[str, Any]] = _LIBRARY_MOVIE_UPGRADE,
) -> None:
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=movies),
    )


def _radarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 2,
        "itype": InstanceType.radarr,
        "batch_size": 10,
        "hourly_cap": 20,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


# ---------------------------------------------------------------------------
# Tests: missing + cutoff interaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_missing_and_cutoff_both_run_combined_count(
    seeded_instances: None,
) -> None:
    """When cutoff is enabled, both passes run and counts are summed."""
    _mock_radarr_missing()
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=True)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_missing_runs_before_cutoff(seeded_instances: None) -> None:
    """Missing pass log rows appear before cutoff pass log rows."""
    _mock_radarr_missing()
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=True)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched"]
    assert len(searched) == 2
    assert searched[0]["search_kind"] == "missing"
    assert searched[1]["search_kind"] == "cutoff"


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_disabled_skips_cutoff_pass(
    seeded_instances: None,
) -> None:
    """With cutoff_enabled=False, only missing pass runs."""
    _mock_radarr_missing()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=False)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    assert all(r["search_kind"] == "missing" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_upgrade_disabled_skips_upgrade_pass(
    seeded_instances: None,
) -> None:
    """With upgrade_enabled=False, upgrade pass is skipped entirely."""
    _mock_radarr_missing()
    _mock_radarr_command()

    inst = _radarr_instance(upgrade_enabled=False)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    assert all(r.get("search_kind") != "upgrade" for r in rows)


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_all_three_passes_run_and_sum(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Missing(1) + cutoff(1) + upgrade(1) = 3."""
    _mock_radarr_missing()
    _mock_radarr_cutoff()
    _mock_radarr_library()
    _mock_radarr_command()

    inst = _radarr_instance(
        cutoff_enabled=True,
        upgrade_enabled=True,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 3


@pytest.mark.asyncio()
@respx.mock
async def test_missing_hourly_cap_does_not_block_cutoff(
    seeded_instances: None,
) -> None:
    """Missing hourly cap is independent: cutoff can still search."""
    _mock_radarr_missing()
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(
        cutoff_enabled=True,
        hourly_cap=1,
        batch_size=1,
        cutoff_hourly_cap=5,
        cutoff_batch_size=5,
    )
    count = await run_instance_search(inst, MASTER_KEY)

    # Missing searches 1 (capped at 1), cutoff searches 1
    assert count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_hourly_cap_independent_of_missing(
    seeded_instances: None,
) -> None:
    """Cutoff hourly cap does not affect missing pass."""
    two_movies = {
        "page": 1,
        "pageSize": 50,
        "totalRecords": 2,
        "records": [
            _MOVIE_RECORD,
            {**_MOVIE_RECORD, "id": 205},
        ],
    }
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=two_movies),
    )
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(
        cutoff_enabled=True,
        hourly_cap=5,
        batch_size=5,
        cutoff_hourly_cap=1,
        cutoff_batch_size=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    missing_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "missing"
    ]
    cutoff_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "cutoff"
    ]
    assert len(missing_searched) == 2
    assert len(cutoff_searched) == 1


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_upgrade_hourly_cap_independent(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Upgrade hourly cap is tracked separately from missing/cutoff."""
    _mock_radarr_missing()
    _mock_radarr_library(
        [
            {
                "id": 210 + i,
                "title": f"Movie {i}",
                "year": 2023,
                "monitored": True,
                "hasFile": True,
                "movieFile": {"qualityCutoffNotMet": False},
                "inCinemas": "2023-01-01T00:00:00Z",
                "physicalRelease": None,
                "digitalRelease": None,
            }
            for i in range(5)
        ]
    )
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_enabled=True,
        upgrade_batch_size=5,
        upgrade_hourly_cap=2,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_searched = [
        r for r in rows if r["action"] == "searched" and r["search_kind"] == "upgrade"
    ]
    # Upgrade cap is min(2, hard_cap=5) = 2
    assert len(upgrade_searched) == 2


@pytest.mark.asyncio()
@respx.mock
async def test_zero_missing_batch_size_returns_zero(
    seeded_instances: None,
) -> None:
    """batch_size=0 means no missing pass, returns 0."""
    inst = _radarr_instance(batch_size=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_zero_cutoff_batch_size_skips_cutoff(
    seeded_instances: None,
) -> None:
    """cutoff_batch_size=0 means cutoff pass is skipped."""
    _mock_radarr_missing()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=True, cutoff_batch_size=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    assert all(r["search_kind"] != "cutoff" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_hourly_cap_zero_means_unlimited_missing(
    seeded_instances: None,
) -> None:
    """hourly_cap=0 disables the cap; all items are searched."""
    three_movies = {
        "page": 1,
        "pageSize": 50,
        "totalRecords": 3,
        "records": [{**_MOVIE_RECORD, "id": 201 + i} for i in range(3)],
    }
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=three_movies),
    )
    _mock_radarr_command()

    inst = _radarr_instance(hourly_cap=0, batch_size=10)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 3


@pytest.mark.asyncio()
@respx.mock
async def test_search_kind_logged_correctly_missing(
    seeded_instances: None,
) -> None:
    """Missing pass rows have search_kind='missing'."""
    _mock_radarr_missing()
    _mock_radarr_command()

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched"]
    assert all(r["search_kind"] == "missing" for r in searched)


@pytest.mark.asyncio()
@respx.mock
async def test_search_kind_logged_correctly_cutoff(
    seeded_instances: None,
) -> None:
    """Cutoff pass rows have search_kind='cutoff'."""
    _mock_radarr_missing(_EMPTY_PAGE)
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=True, batch_size=10)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    cutoff_rows = [r for r in rows if r["search_kind"] == "cutoff"]
    assert len(cutoff_rows) >= 1
    assert cutoff_rows[0]["action"] == "searched"


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_search_kind_logged_correctly_upgrade(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Upgrade pass rows have search_kind='upgrade'."""
    _mock_radarr_missing(_EMPTY_PAGE)
    _mock_radarr_library()
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_enabled=True,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    upgrade_rows = [r for r in rows if r["search_kind"] == "upgrade"]
    assert len(upgrade_rows) >= 1
    assert upgrade_rows[0]["action"] == "searched"


@pytest.mark.asyncio()
@respx.mock
async def test_cycle_id_shared_across_all_passes(
    seeded_instances: None,
) -> None:
    """All log rows within a cycle share the same cycle_id."""
    _mock_radarr_missing()
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=True)
    await run_instance_search(inst, MASTER_KEY, cycle_id="test-cycle-1")

    rows = await get_log_rows()
    assert len(rows) >= 2
    cycle_ids = {r["cycle_id"] for r in rows}
    assert cycle_ids == {"test-cycle-1"}


@pytest.mark.asyncio()
@respx.mock
async def test_cycle_trigger_propagated(seeded_instances: None) -> None:
    """cycle_trigger='run_now' is recorded in all log rows."""
    _mock_radarr_missing()
    _mock_radarr_command()

    inst = _radarr_instance()
    await run_instance_search(
        inst,
        MASTER_KEY,
        cycle_trigger="run_now",
    )

    rows = await get_log_rows()
    assert all(r["cycle_trigger"] == "run_now" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_fewer_candidates_than_batch(
    seeded_instances: None,
) -> None:
    """When fewer items exist than batch_size, all are searched."""
    _mock_radarr_missing()
    _mock_radarr_command()

    inst = _radarr_instance(batch_size=10)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_empty_missing_list_still_runs_cutoff(
    seeded_instances: None,
) -> None:
    """Empty missing list does not block cutoff pass."""
    _mock_radarr_missing(_EMPTY_PAGE)
    _mock_radarr_cutoff()
    _mock_radarr_command()

    inst = _radarr_instance(cutoff_enabled=True)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    assert any(r["search_kind"] == "cutoff" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_missing_dispatch_error_does_not_prevent_cutoff(
    seeded_instances: None,
) -> None:
    """If missing dispatch errors, cutoff still runs."""
    _mock_radarr_missing()
    _mock_radarr_cutoff()
    # Missing dispatch fails, cutoff succeeds
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        side_effect=[
            httpx.Response(500, json={"error": "fail"}),
            httpx.Response(201, json=_COMMAND_RESP),
        ],
    )

    inst = _radarr_instance(cutoff_enabled=True)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    cutoff_searched = [
        r for r in rows if r["search_kind"] == "cutoff" and r["action"] == "searched"
    ]
    assert len(cutoff_searched) == 1


@pytest.mark.asyncio()
@respx.mock
@patch(
    "houndarr.engine.search_loop.update_instance",
    new_callable=AsyncMock,
)
async def test_empty_upgrade_pool_logs_info(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """Empty upgrade pool logs an info row with 'upgrade pool empty'."""
    _mock_radarr_missing(_EMPTY_PAGE)
    _mock_radarr_library([])
    _mock_radarr_command()

    inst = _radarr_instance(
        upgrade_enabled=True,
        upgrade_batch_size=1,
        upgrade_hourly_cap=5,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    info_rows = [r for r in rows if r["action"] == "info" and r["search_kind"] == "upgrade"]
    assert len(info_rows) == 1
    assert "upgrade pool empty" in (info_rows[0].get("message") or "")


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_uses_cutoff_cooldown_days(
    seeded_instances: None,
) -> None:
    """Cutoff pass uses cutoff_cooldown_days, not cooldown_days."""
    from houndarr.services.cooldown import record_search

    # Put item 202 on cooldown
    await record_search(2, 202, "movie")

    _mock_radarr_missing(_EMPTY_PAGE)
    _mock_radarr_cutoff()
    _mock_radarr_command()

    # cooldown_days=7 (missing), cutoff_cooldown_days=0 (disabled)
    inst = _radarr_instance(
        cutoff_enabled=True,
        cooldown_days=7,
        cutoff_cooldown_days=0,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    cutoff_searched = [
        r for r in rows if r["search_kind"] == "cutoff" and r["action"] == "searched"
    ]
    # cutoff_cooldown_days=0 disables cooldown, so item is searched
    assert len(cutoff_searched) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_hourly_cap_zero_means_unlimited_cutoff(
    seeded_instances: None,
) -> None:
    """cutoff_hourly_cap=0 means no limit on cutoff searches."""
    three_cutoff = {
        "page": 1,
        "pageSize": 25,
        "totalRecords": 3,
        "records": [{**_MOVIE_RECORD, "id": 220 + i, "title": f"Cutoff {i}"} for i in range(3)],
    }
    _mock_radarr_missing(_EMPTY_PAGE)
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=three_cutoff),
    )
    _mock_radarr_command()

    inst = _radarr_instance(
        cutoff_enabled=True,
        cutoff_hourly_cap=0,
        cutoff_batch_size=10,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    cutoff_searched = [
        r for r in rows if r["search_kind"] == "cutoff" and r["action"] == "searched"
    ]
    assert len(cutoff_searched) == 3


@pytest.mark.asyncio()
@respx.mock
async def test_empty_missing_and_cutoff_returns_zero(
    seeded_instances: None,
) -> None:
    """Empty results for both passes returns 0."""
    _mock_radarr_missing(_EMPTY_PAGE)
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )

    inst = _radarr_instance(cutoff_enabled=True)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0

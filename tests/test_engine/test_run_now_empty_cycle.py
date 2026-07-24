"""Tests for the run-now empty-cycle info row in run_instance_search()."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import InstanceType

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    MASTER_KEY,
    SONARR_URL,
    get_log_rows,
    make_instance,
)

_EMPTY_WANTED: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 0,
    "records": [],
}

_MISSING_SONARR_ONE: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_EPISODE_RECORD],
}


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 10,
        "hourly_cap": 20,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_empty_cycle_writes_info_row(seeded_instances: None) -> None:
    """A manual cycle that produced no rows leaves one visible info row."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_WANTED)
    )

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY, cycle_trigger="run_now")

    assert count == 0
    rows = await get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "info"
    assert rows[0]["instance_id"] == 1
    assert rows[0]["item_id"] is None
    assert rows[0]["cycle_trigger"] == "run_now"
    # Message-only: a reason would shadow the message in the logs page,
    # which renders `reason or message`.
    assert rows[0]["reason"] is None
    assert rows[0]["message"] == "Run now finished: no wanted items to evaluate"


@pytest.mark.asyncio()
@respx.mock
async def test_scheduled_empty_cycle_stays_quiet(seeded_instances: None) -> None:
    """Scheduled cycles keep their current no-row behaviour on empty fetches."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_WANTED)
    )

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY, cycle_trigger="scheduled")

    assert count == 0
    assert await get_log_rows() == []


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_with_skip_row_writes_no_info_row(seeded_instances: None) -> None:
    """When the cycle already produced a row, the epilogue adds nothing."""
    from houndarr.services.cooldown import record_search

    await record_search(1, 101, "episode")
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=_MISSING_SONARR_ONE),
            httpx.Response(200, json=_EMPTY_WANTED),
        ]
    )

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY, cycle_trigger="run_now")

    assert count == 0
    rows = await get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "skipped"
    assert rows[0]["reason"] == "on cooldown (7d)"


@pytest.mark.asyncio()
async def test_run_now_all_passes_disabled_names_the_cause(seeded_instances: None) -> None:
    """With every search pass off, the info row says so instead of claiming
    an empty wanted list (no HTTP call is made at all in this state)."""
    instance = _sonarr_instance(missing_enabled=False)

    count = await run_instance_search(instance, MASTER_KEY, cycle_trigger="run_now")

    assert count == 0
    rows = await get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "info"
    assert rows[0]["reason"] is None
    assert rows[0]["message"] == "Run now finished: every search pass is disabled for this instance"


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_with_search_writes_no_info_row(seeded_instances: None) -> None:
    """A manual cycle that dispatched a search never appends the info row."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=_MISSING_SONARR_ONE),
            httpx.Response(200, json=_EMPTY_WANTED),
        ]
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY, cycle_trigger="run_now")

    assert count == 1
    rows = await get_log_rows()
    assert [r["action"] for r in rows] == ["searched"]

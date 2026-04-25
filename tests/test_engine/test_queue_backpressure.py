"""Tests for queue backpressure gate in run_instance_search()."""

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MISSING_SONARR_1: dict[str, Any] = {
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


def _mock_queue_status(total_count: int) -> None:
    respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        return_value=httpx.Response(
            200,
            json={"totalCount": total_count},
        ),
    )


def _mock_missing_and_command() -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR_1),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_queue_at_exactly_limit_skips_cycle(
    seeded_instances: None,
) -> None:
    """totalCount == queue_limit means backpressure: return 0."""
    _mock_queue_status(5)
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_queue_at_limit_minus_one_proceeds(
    seeded_instances: None,
) -> None:
    """totalCount < queue_limit allows the search to proceed."""
    _mock_queue_status(4)
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_queue_above_limit_skips(seeded_instances: None) -> None:
    """totalCount > queue_limit skips the entire cycle."""
    _mock_queue_status(10)
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_queue_limit_zero_disables_check(
    seeded_instances: None,
) -> None:
    """queue_limit=0 means no queue check; search runs normally."""
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_queue_transport_error_fails_open(
    seeded_instances: None,
) -> None:
    """ConnectError on queue endpoint lets search proceed (fail open)."""
    respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_queue_http_500_fails_open(seeded_instances: None) -> None:
    """HTTP 500 from queue endpoint lets search proceed (fail open)."""
    respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        return_value=httpx.Response(500, text="Internal Server Error"),
    )
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_queue_missing_total_count_fails_open(
    seeded_instances: None,
) -> None:
    """Response without totalCount raises KeyError, caught, proceeds."""
    respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json={}),
    )
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_queue_backpressure_logs_info_action(
    seeded_instances: None,
) -> None:
    """Backpressure skip writes an action='info' log row."""
    _mock_queue_status(10)
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    info_rows = [r for r in rows if r["action"] == "info"]
    assert len(info_rows) == 1
    assert "backpressure" in (info_rows[0].get("reason") or "")


@pytest.mark.asyncio()
@respx.mock
async def test_queue_backpressure_log_includes_counts(
    seeded_instances: None,
) -> None:
    """Backpressure log message includes queue count and limit."""
    _mock_queue_status(10)
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    info_rows = [r for r in rows if r["action"] == "info"]
    msg = info_rows[0].get("message") or ""
    assert "10" in msg
    assert "5" in msg


@pytest.mark.asyncio()
@respx.mock
async def test_queue_check_not_called_when_limit_zero(
    seeded_instances: None,
) -> None:
    """When queue_limit=0, no request is made to queue/status."""
    queue_route = respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json={"totalCount": 99}),
    )
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=0)
    await run_instance_search(inst, MASTER_KEY)

    assert queue_route.call_count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_queue_below_limit_proceeds_and_searches(
    seeded_instances: None,
) -> None:
    """totalCount=0 with positive limit lets search run normally."""
    _mock_queue_status(0)
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_queue_invalid_url_fails_open(
    seeded_instances: None,
) -> None:
    """httpx.InvalidURL from queue endpoint lets search proceed."""
    respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        side_effect=httpx.InvalidURL("bad url"),
    )
    _mock_missing_and_command()

    inst = _sonarr_instance(queue_limit=5)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1

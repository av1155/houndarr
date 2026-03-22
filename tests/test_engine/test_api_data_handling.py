"""Tests for edge cases in how the engine handles unusual *arr API data."""

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


def _movie(movie_id: int = 201, **overrides: Any) -> dict[str, Any]:
    base = {**_MOVIE_RECORD, "id": movie_id}
    base.update(overrides)
    return base


def _radarr_missing_page(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "page": 1,
        "pageSize": 50,
        "totalRecords": len(records),
        "records": records,
    }


# ---------------------------------------------------------------------------
# Empty list tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_empty_missing_list_no_searches(
    seeded_instances: None,
) -> None:
    """Sonarr returns empty records: count=0, no error logs."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )

    inst = _sonarr_instance()
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    assert not any(r["action"] == "error" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_empty_cutoff_list_no_searches(
    seeded_instances: None,
) -> None:
    """cutoff_enabled with empty cutoff list: count=0 for cutoff pass."""
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )

    inst = _radarr_instance(cutoff_enabled=True)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


# ---------------------------------------------------------------------------
# Radarr release anchor fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_digital_release_preferred(
    seeded_instances: None,
) -> None:
    """Movie with past digitalRelease is eligible for search."""
    movie = _movie(
        digitalRelease="2023-01-15T00:00:00Z",
        physicalRelease="2023-02-01T00:00:00Z",
        inCinemas="2022-12-01T00:00:00Z",
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_physical_release_fallback(
    seeded_instances: None,
) -> None:
    """digitalRelease=None, past physicalRelease: eligible."""
    movie = _movie(
        digitalRelease=None,
        physicalRelease="2023-02-01T00:00:00Z",
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_release_date_fallback(
    seeded_instances: None,
) -> None:
    """digital+physical None, past releaseDate: eligible."""
    movie = _movie(
        digitalRelease=None,
        physicalRelease=None,
        releaseDate="2023-03-01T00:00:00Z",
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_in_cinemas_last_fallback(
    seeded_instances: None,
) -> None:
    """Only past inCinemas set: eligible."""
    movie = _movie(
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas="2023-01-01T00:00:00Z",
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_all_dates_none_treated_as_released(
    seeded_instances: None,
) -> None:
    """All date fields None: treated as released (eligible)."""
    movie = _movie(
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_malformed_date_treated_as_released(
    seeded_instances: None,
) -> None:
    """digitalRelease='not-a-date': eligible (parse failure = released)."""
    movie = _movie(
        digitalRelease="not-a-date",
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


# ---------------------------------------------------------------------------
# Radarr unreleased status checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_tba_not_available_skipped(
    seeded_instances: None,
) -> None:
    """status='tba', isAvailable=None: skipped as unreleased."""
    movie = _movie(
        status="tba",
        isAvailable=None,
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    skipped = [r for r in rows if r["action"] == "skipped"]
    assert len(skipped) >= 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_announced_not_available_skipped(
    seeded_instances: None,
) -> None:
    """status='announced', isAvailable=False: skipped."""
    movie = _movie(
        status="announced",
        isAvailable=False,
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_future_year_not_available_skipped(
    seeded_instances: None,
) -> None:
    """year=2999, status='', isAvailable=None: skipped as future title."""
    movie = _movie(
        year=2999,
        status="",
        isAvailable=None,
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_released_status_overrides_future_year(
    seeded_instances: None,
) -> None:
    """year=2999, status='released': eligible despite future year."""
    movie = _movie(
        year=2999,
        status="released",
        isAvailable=None,
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_is_available_true_overrides_tba(
    seeded_instances: None,
) -> None:
    """isAvailable=True, status='tba': eligible (available overrides)."""
    movie = _movie(
        status="tba",
        isAvailable=True,
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_not_available_false_skipped(
    seeded_instances: None,
) -> None:
    """isAvailable=False explicitly: skipped as not available."""
    movie = _movie(
        status="released",
        isAvailable=False,
        digitalRelease=None,
        physicalRelease=None,
        releaseDate=None,
        inCinemas=None,
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_radarr_missing_page([movie]),
        ),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


# ---------------------------------------------------------------------------
# Sonarr null air date
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_null_air_date_eligible(
    seeded_instances: None,
) -> None:
    """airDateUtc=None: eligible (treated as released)."""
    ep = {**_EPISODE_RECORD, "airDateUtc": None}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json={
                "page": 1,
                "pageSize": 10,
                "totalRecords": 1,
                "records": [ep],
            },
        ),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


# ---------------------------------------------------------------------------
# Dispatch error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_dispatch_http_500_logs_error_continues(
    seeded_instances: None,
) -> None:
    """Search POST returns 500: error logged, pass continues."""
    two_movies = _radarr_missing_page(
        [
            _movie(201),
            _movie(202),
        ]
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=two_movies),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        side_effect=[
            httpx.Response(500, text="Internal Server Error"),
            httpx.Response(201, json=_COMMAND_RESP),
        ],
    )

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    errors = [r for r in rows if r["action"] == "error"]
    searched = [r for r in rows if r["action"] == "searched"]
    assert len(errors) == 1
    assert len(searched) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_dispatch_http_400_logs_error_continues(
    seeded_instances: None,
) -> None:
    """Search POST returns 400: error logged, pass continues."""
    two_movies = _radarr_missing_page(
        [
            _movie(201),
            _movie(202),
        ]
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=two_movies),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        side_effect=[
            httpx.Response(400, text="Bad Request"),
            httpx.Response(201, json=_COMMAND_RESP),
        ],
    )

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    errors = [r for r in rows if r["action"] == "error"]
    searched = [r for r in rows if r["action"] == "searched"]
    assert len(errors) == 1
    assert len(searched) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_dispatch_transport_error_logs_error_continues(
    seeded_instances: None,
) -> None:
    """ConnectError during dispatch: error logged, pass continues."""
    two_movies = _radarr_missing_page(
        [
            _movie(201),
            _movie(202),
        ]
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=two_movies),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.Response(201, json=_COMMAND_RESP),
        ],
    )

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    errors = [r for r in rows if r["action"] == "error"]
    searched = [r for r in rows if r["action"] == "searched"]
    assert len(errors) == 1
    assert len(searched) == 1

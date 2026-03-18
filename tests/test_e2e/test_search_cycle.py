"""End-to-end integration tests for the full search cycle.

These tests exercise the complete path from instance creation through the
supervisor running one search loop iteration to verifying that search_log
rows and cooldown records are written correctly.  All external HTTP calls
are intercepted by respx.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
import respx
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine import supervisor as _supervisor_mod
from houndarr.engine.search_loop import run_instance_search
from houndarr.engine.supervisor import Supervisor
from houndarr.services.cooldown import record_search
from houndarr.services.instances import Instance, InstanceType, create_instance

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
LIDARR_URL = "http://lidarr:8686"
READARR_URL = "http://readarr:8787"
WHISPARR_URL = "http://whisparr:6969"

_EPISODE: dict[str, Any] = {
    "id": 101,
    "title": "Pilot",
    "seasonNumber": 1,
    "episodeNumber": 1,
    "airDateUtc": "2023-09-01T00:00:00Z",
    "series": {"title": "My Show"},
}
_MOVIE: dict[str, Any] = {
    "id": 201,
    "title": "My Movie",
    "year": 2023,
    "digitalRelease": None,
}
_ALBUM: dict[str, Any] = {
    "id": 301,
    "artistId": 50,
    "title": "Greatest Hits",
    "releaseDate": "2023-03-15T00:00:00Z",
    "artist": {"id": 50, "artistName": "Test Artist"},
}
_BOOK: dict[str, Any] = {
    "id": 401,
    "authorId": 60,
    "title": "Test Book",
    "releaseDate": "2023-06-01T00:00:00Z",
    "author": {"id": 60, "authorName": "Test Author"},
}
_WHISPARR_EP: dict[str, Any] = {
    "id": 501,
    "seriesId": 70,
    "title": "Scene Title",
    "seasonNumber": 1,
    "absoluteEpisodeNumber": 5,
    "releaseDate": {"year": 2023, "month": 9, "day": 1},
    "series": {"id": 70, "title": "My Whisparr Show"},
}

_MISSING_SONARR_1 = {"records": [_EPISODE]}
_MISSING_RADARR_1 = {"records": [_MOVIE]}
_MISSING_LIDARR_1 = {"records": [_ALBUM]}
_MISSING_READARR_1 = {"records": [_BOOK]}
_MISSING_WHISPARR_1 = {"records": [_WHISPARR_EP]}
_MISSING_EMPTY = {"records": []}
_FUTURE_AIR_DATE = "2999-01-01T00:00:00Z"

_CMD_OK = {"id": 1, "name": "EpisodeSearch"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def master_key() -> bytes:
    """Generate a fresh Fernet key for each test."""
    return Fernet.generate_key()


@pytest_asyncio.fixture()
async def sonarr_instance(db: None, master_key: bytes) -> Instance:
    """Create a real Sonarr instance row (with encrypted API key)."""
    return await create_instance(
        master_key=master_key,
        name="E2E Sonarr",
        type=InstanceType.sonarr,
        url=SONARR_URL,
        api_key="sonarr-key",
        batch_size=5,
        hourly_cap=10,
        cooldown_days=7,
        sleep_interval_mins=15,
    )


@pytest_asyncio.fixture()
async def radarr_instance(db: None, master_key: bytes) -> Instance:
    """Create a real Radarr instance row (with encrypted API key)."""
    return await create_instance(
        master_key=master_key,
        name="E2E Radarr",
        type=InstanceType.radarr,
        url=RADARR_URL,
        api_key="radarr-key",
        batch_size=5,
        hourly_cap=10,
        cooldown_days=7,
        sleep_interval_mins=15,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _log_rows() -> list[dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM search_log ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _cooldown_rows(instance_id: int) -> list[dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM cooldowns WHERE instance_id = ? ORDER BY id ASC",
            (instance_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Test 1 — Full cycle: items searched, log written, cooldowns recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_full_cycle_sonarr(sonarr_instance: Instance, master_key: bytes) -> None:
    """One complete Sonarr search cycle — item is searched, log and cooldown written."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR_1)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(return_value=httpx.Response(201, json=_CMD_OK))

    count = await run_instance_search(sonarr_instance, master_key)

    assert count == 1

    # search_log must have exactly one 'searched' row
    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0]["action"] == "searched"
    assert logs[0]["item_id"] == 101
    assert logs[0]["item_type"] == "episode"
    assert logs[0]["instance_id"] == sonarr_instance.id

    # cooldowns must have one row for episode 101
    cds = await _cooldown_rows(sonarr_instance.id)
    assert len(cds) == 1
    assert cds[0]["item_id"] == 101
    assert cds[0]["item_type"] == "episode"


@pytest.mark.asyncio()
@respx.mock
async def test_full_cycle_radarr(radarr_instance: Instance, master_key: bytes) -> None:
    """One complete Radarr search cycle — movie is searched, log and cooldown written."""
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RADARR_1)
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )

    count = await run_instance_search(radarr_instance, master_key)

    assert count == 1

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0]["action"] == "searched"
    assert logs[0]["item_id"] == 201
    assert logs[0]["item_type"] == "movie"

    cds = await _cooldown_rows(radarr_instance.id)
    assert len(cds) == 1
    assert cds[0]["item_id"] == 201


# ---------------------------------------------------------------------------
# Test 2 — Second cycle: same items skipped (on cooldown)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_second_cycle_items_skipped_on_cooldown(
    sonarr_instance: Instance, master_key: bytes
) -> None:
    """Running the search loop twice: second run must skip items already on cooldown."""
    # Both cycles return the same episode
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR_1)
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_CMD_OK)
    )

    # First cycle — item is searched
    count1 = await run_instance_search(sonarr_instance, master_key)
    assert count1 == 1
    assert search_route.call_count == 1

    # Second cycle — same item, now on cooldown → should be skipped
    count2 = await run_instance_search(sonarr_instance, master_key)
    assert count2 == 0
    # search endpoint called exactly once total (first cycle only)
    assert search_route.call_count == 1

    logs = await _log_rows()
    actions = [r["action"] for r in logs]
    assert "searched" in actions
    assert "skipped" in actions

    # cooldown row must still be exactly one (upsert, not duplicate)
    cds = await _cooldown_rows(sonarr_instance.id)
    assert len(cds) == 1


# ---------------------------------------------------------------------------
# Test 3 — Hourly cap enforced across a cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_hourly_cap_enforced(
    db: None,
    master_key: bytes,  # noqa: ARG001
) -> None:
    """When hourly cap is already exhausted, the next item is skipped immediately."""
    # Create an instance with cap=1
    instance = await create_instance(
        master_key=master_key,
        name="Cap Test",
        type=InstanceType.sonarr,
        url=SONARR_URL,
        api_key="key",
        batch_size=5,
        hourly_cap=1,
        cooldown_days=7,
        sleep_interval_mins=15,
    )

    # Two episodes returned
    two_episodes = {
        "records": [
            _EPISODE,
            {**_EPISODE, "id": 102, "title": "Episode 2"},
        ]
    }
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=two_episodes)
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_CMD_OK)
    )

    count = await run_instance_search(instance, master_key)

    # Only one searched before cap is hit
    assert count == 1
    assert search_route.call_count == 1

    logs = await _log_rows()
    actions = [r["action"] for r in logs]
    assert actions.count("searched") == 1
    assert actions.count("skipped") == 1
    skipped = next(r for r in logs if r["action"] == "skipped")
    assert "hourly cap" in (skipped["reason"] or "")


# ---------------------------------------------------------------------------
# Test 4 — Graceful shutdown: supervisor cancels tasks without error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_supervisor_graceful_shutdown(
    sonarr_instance: Instance,
    master_key: bytes,  # noqa: ARG001
) -> None:
    """Supervisor tasks are cancelled on stop() with no unhandled exceptions."""
    # The search loop will block on get_missing; we just need it to be running
    # long enough for us to cancel it.
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(side_effect=httpx.ConnectError("blocked"))

    sup = Supervisor(master_key=master_key)
    await sup.start()
    assert len(sup._tasks) == 1  # noqa: SLF001

    # Give the task a moment to spin up and hit the (failing) HTTP call
    await asyncio.sleep(0.05)

    # stop() must complete without raising
    await sup.stop()
    assert sup._tasks == {}  # noqa: SLF001


@pytest.mark.asyncio()
async def test_supervisor_no_instances_starts_cleanly(db: None, master_key: bytes) -> None:
    """Supervisor with zero instances should start and stop without error."""
    sup = Supervisor(master_key=master_key)
    await sup.start()
    assert sup._tasks == {}  # noqa: SLF001
    await sup.stop()  # must be a no-op


# ---------------------------------------------------------------------------
# Test 5 — Both Radarr and Sonarr instances run concurrently via supervisor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_supervisor_runs_both_instances(
    sonarr_instance: Instance,
    radarr_instance: Instance,
    master_key: bytes,
) -> None:
    """Supervisor spawns separate tasks for Radarr and Sonarr instances."""
    # Sonarr: returns one episode then empty (second cycle won't fire, but mock it anyway)
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR_1)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(return_value=httpx.Response(201, json=_CMD_OK))

    # Radarr: returns one movie
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RADARR_1)
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )

    with patch.object(_supervisor_mod, "_STARTUP_GRACE_SECS", 0):
        sup = Supervisor(master_key=master_key)
        await sup.start()
        assert len(sup._tasks) == 2  # noqa: SLF001

        # Let both tasks complete their first search cycle before stopping
        await asyncio.sleep(0.2)
        await sup.stop()

    # Both instances must have a 'searched' log entry
    logs = await _log_rows()
    # Filter out the supervisor info row (instance_id=None)
    searched = [r for r in logs if r["action"] == "searched"]
    instance_ids = {r["instance_id"] for r in searched}
    assert sonarr_instance.id in instance_ids
    assert radarr_instance.id in instance_ids


@pytest.mark.asyncio()
@respx.mock
async def test_missing_pass_reaches_deeper_page_when_top_items_are_ineligible(
    sonarr_instance: Instance, master_key: bytes
) -> None:
    """Fair scanning should reach page 2 when page 1 candidates are blocked."""
    await record_search(sonarr_instance.id, 601, "episode")

    page_1 = {
        "records": [
            {**_EPISODE, "id": 600, "airDateUtc": _FUTURE_AIR_DATE},
            {**_EPISODE, "id": 601, "airDateUtc": "2023-09-01T00:00:00Z"},
        ]
    }
    page_2 = {"records": [{**_EPISODE, "id": 602, "title": "Deeper candidate"}]}

    missing_route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=page_1),
            httpx.Response(200, json=page_2),
        ]
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_CMD_OK)
    )

    # Use a small target to ensure we stop once we find one eligible candidate.
    sonarr_instance.batch_size = 1
    count = await run_instance_search(sonarr_instance, master_key)

    assert count == 1
    assert missing_route.call_count == 2
    assert search_route.call_count == 1

    logs = await _log_rows()
    assert any(row["item_id"] == 602 and row["action"] == "searched" for row in logs)


@pytest.mark.asyncio()
@respx.mock
async def test_missing_list_calls_are_bounded_per_cycle(
    sonarr_instance: Instance, master_key: bytes
) -> None:
    """Fair scanning keeps missing list-page fetches under a hard cap."""
    payloads = [
        {
            "records": [
                {**_EPISODE, "id": i, "airDateUtc": _FUTURE_AIR_DATE}
                for i in range(start, start + 10)
            ]
        }
        for start in (900, 1000, 1100, 1200)
    ]

    missing_route = respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[httpx.Response(200, json=payload) for payload in payloads]
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_CMD_OK)
    )

    sonarr_instance.batch_size = 2
    count = await run_instance_search(sonarr_instance, master_key)

    assert count == 0
    assert missing_route.call_count == 3
    assert not search_route.called


# ---------------------------------------------------------------------------
# Fixtures — Lidarr, Readarr, Whisparr
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def lidarr_instance(db: None, master_key: bytes) -> Instance:
    """Create a real Lidarr instance row (with encrypted API key)."""
    return await create_instance(
        master_key=master_key,
        name="E2E Lidarr",
        type=InstanceType.lidarr,
        url=LIDARR_URL,
        api_key="lidarr-key",
        batch_size=5,
        hourly_cap=10,
        cooldown_days=7,
        sleep_interval_mins=15,
    )


@pytest_asyncio.fixture()
async def readarr_instance(db: None, master_key: bytes) -> Instance:
    """Create a real Readarr instance row (with encrypted API key)."""
    return await create_instance(
        master_key=master_key,
        name="E2E Readarr",
        type=InstanceType.readarr,
        url=READARR_URL,
        api_key="readarr-key",
        batch_size=5,
        hourly_cap=10,
        cooldown_days=7,
        sleep_interval_mins=15,
    )


@pytest_asyncio.fixture()
async def whisparr_instance(db: None, master_key: bytes) -> Instance:
    """Create a real Whisparr instance row (with encrypted API key)."""
    return await create_instance(
        master_key=master_key,
        name="E2E Whisparr",
        type=InstanceType.whisparr,
        url=WHISPARR_URL,
        api_key="whisparr-key",
        batch_size=5,
        hourly_cap=10,
        cooldown_days=7,
        sleep_interval_mins=15,
    )


# ---------------------------------------------------------------------------
# Test — Full cycle: Lidarr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_full_cycle_lidarr(lidarr_instance: Instance, master_key: bytes) -> None:
    """One complete Lidarr search cycle — album is searched, log and cooldown written."""
    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_LIDARR_1)
    )
    respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 3})
    )

    count = await run_instance_search(lidarr_instance, master_key)

    assert count == 1
    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0]["action"] == "searched"
    assert logs[0]["item_id"] == 301
    assert logs[0]["item_type"] == "album"
    assert logs[0]["instance_id"] == lidarr_instance.id

    cds = await _cooldown_rows(lidarr_instance.id)
    assert len(cds) == 1
    assert cds[0]["item_id"] == 301
    assert cds[0]["item_type"] == "album"


# ---------------------------------------------------------------------------
# Test — Full cycle: Readarr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_full_cycle_readarr(readarr_instance: Instance, master_key: bytes) -> None:
    """One complete Readarr search cycle — book is searched, log and cooldown written."""
    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_READARR_1)
    )
    respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 4})
    )

    count = await run_instance_search(readarr_instance, master_key)

    assert count == 1
    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0]["action"] == "searched"
    assert logs[0]["item_id"] == 401
    assert logs[0]["item_type"] == "book"

    cds = await _cooldown_rows(readarr_instance.id)
    assert len(cds) == 1
    assert cds[0]["item_id"] == 401
    assert cds[0]["item_type"] == "book"


# ---------------------------------------------------------------------------
# Test — Full cycle: Whisparr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_full_cycle_whisparr(whisparr_instance: Instance, master_key: bytes) -> None:
    """One complete Whisparr search cycle — episode is searched, log and cooldown written."""
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_WHISPARR_1)
    )
    respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 5})
    )

    count = await run_instance_search(whisparr_instance, master_key)

    assert count == 1
    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0]["action"] == "searched"
    assert logs[0]["item_id"] == 501
    assert logs[0]["item_type"] == "whisparr_episode"

    cds = await _cooldown_rows(whisparr_instance.id)
    assert len(cds) == 1
    assert cds[0]["item_id"] == 501
    assert cds[0]["item_type"] == "whisparr_episode"

"""Tests for GET /api/logs filter validation and edge cases.

Complements test_logs.py with input-validation boundaries (422 on invalid
search_kind/cycle_trigger/hide_system), the 'upgrade' search_kind filter,
and empty-param handling.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient

from houndarr.database import get_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def seeded_filter_data(db: None) -> AsyncGenerator[None, None]:
    """Seed search_log with rows covering all search_kind and cycle_trigger values."""
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
                (2, "Radarr Test", "radarr", "http://radarr:7878"),
            ],
        )
        await conn.executemany(
            """
            INSERT INTO search_log
                (instance_id, item_id, item_type, search_kind,
                 cycle_id, cycle_trigger, action, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                # missing + scheduled
                (
                    1,
                    101,
                    "episode",
                    "missing",
                    "c-1",
                    "scheduled",
                    "searched",
                    "2024-01-01T12:00:00.000Z",
                ),
                # cutoff + scheduled
                (
                    1,
                    102,
                    "episode",
                    "cutoff",
                    "c-1",
                    "scheduled",
                    "skipped",
                    "2024-01-01T12:01:00.000Z",
                ),
                # upgrade + scheduled
                (
                    1,
                    103,
                    "episode",
                    "upgrade",
                    "c-2",
                    "scheduled",
                    "searched",
                    "2024-01-01T12:02:00.000Z",
                ),
                # missing + run_now
                (
                    2,
                    201,
                    "movie",
                    "missing",
                    "c-3",
                    "run_now",
                    "searched",
                    "2024-01-01T12:03:00.000Z",
                ),
                # missing + run_now (error)
                (2, 202, "movie", "missing", "c-3", "run_now", "error", "2024-01-01T12:04:00.000Z"),
                # system info row (no instance, no search_kind)
                (None, None, None, None, None, "system", "info", "2024-01-01T11:59:00.000Z"),
            ],
        )
        await conn.commit()
    yield


async def _login_async(client: AsyncClient) -> None:
    await client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


# ---------------------------------------------------------------------------
# Validation: invalid search_kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_invalid_search_kind_returns_422(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """An unrecognised search_kind value triggers a 422 response."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?search_kind=bogus")
    assert resp.status_code == 422


@pytest.mark.asyncio()
async def test_invalid_search_kind_partial_returns_422(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """The HTMX partial endpoint also validates search_kind."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs/partial?search_kind=bogus")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Validation: invalid cycle_trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_invalid_cycle_trigger_returns_422(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """An unrecognised cycle_trigger value triggers a 422 response."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?cycle_trigger=bogus")
    assert resp.status_code == 422


@pytest.mark.asyncio()
async def test_invalid_cycle_trigger_partial_returns_422(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """The HTMX partial endpoint also validates cycle_trigger."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs/partial?cycle_trigger=bogus")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Validation: invalid hide_system
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_invalid_hide_system_returns_422(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """A non-boolean hide_system value triggers a 422 response."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?hide_system=maybe")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# search_kind filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_filter_search_kind_missing(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """search_kind=missing returns only rows with search_kind='missing'."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?search_kind=missing&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert all(r["search_kind"] == "missing" for r in data)


@pytest.mark.asyncio()
async def test_filter_search_kind_upgrade(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """search_kind=upgrade returns only the upgrade row."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?search_kind=upgrade&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["search_kind"] == "upgrade"
    assert data[0]["item_id"] == 103


# ---------------------------------------------------------------------------
# cycle_trigger filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_filter_cycle_trigger_scheduled(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """cycle_trigger=scheduled returns only scheduled rows."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?cycle_trigger=scheduled&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert all(r["cycle_trigger"] == "scheduled" for r in data)


@pytest.mark.asyncio()
async def test_filter_cycle_trigger_system(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """cycle_trigger=system returns only system lifecycle rows."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?cycle_trigger=system&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["action"] == "info"
    assert data[0]["instance_id"] is None


# ---------------------------------------------------------------------------
# Empty-param handling (no filter applied)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_empty_search_kind_treated_as_no_filter(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """Empty search_kind param (HTMX default) returns all rows."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?search_kind=&limit=200")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


@pytest.mark.asyncio()
async def test_empty_cycle_trigger_treated_as_no_filter(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """Empty cycle_trigger param returns all rows."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?cycle_trigger=&limit=200")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_combined_search_kind_and_cycle_trigger(
    seeded_filter_data: None, async_client: AsyncClient
) -> None:
    """Combining search_kind + cycle_trigger narrows to the intersection."""
    await _login_async(async_client)
    resp = await async_client.get("/api/logs?search_kind=missing&cycle_trigger=run_now&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for row in data:
        assert row["search_kind"] == "missing"
        assert row["cycle_trigger"] == "run_now"

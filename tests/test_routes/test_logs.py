"""Tests for GET /api/logs, GET /api/logs/partial, and GET /logs."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient
from houndarr.database import get_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUTH_LOCATIONS = {"/setup", "/login", "http://testserver/setup", "http://testserver/login"}

_VALID_FORM = {
    "name": "My Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "api_key": "test-api-key",
    "connection_verified": "true",
}


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _always_true(self: ArrClient) -> bool:
        return True

    monkeypatch.setattr(ArrClient, "ping", _always_true)


def _login(client: TestClient) -> None:
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


@pytest_asyncio.fixture()
async def seeded_log(db: None) -> AsyncGenerator[None, None]:  # type: ignore[misc]
    """Seed search_log with rows across two instances for filter/pagination tests."""
    async with get_db() as conn:
        # Seed two instances so FK constraint on search_log is satisfied
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
                (2, "Radarr Test", "radarr", "http://radarr:7878"),
            ],
        )
        # Seed a variety of log rows
        await conn.executemany(
            """
            INSERT INTO search_log
                (instance_id, item_id, item_type, action, reason, message, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 101, "episode", "searched", None, None, "2024-01-01T12:00:00.000Z"),
                (
                    1,
                    102,
                    "episode",
                    "skipped",
                    "on cooldown (7d)",
                    None,
                    "2024-01-01T12:01:00.000Z",
                ),
                (2, 201, "movie", "searched", None, None, "2024-01-01T12:02:00.000Z"),
                (2, 202, "movie", "error", None, "connection refused", "2024-01-01T12:03:00.000Z"),
                (
                    None,
                    None,
                    None,
                    "info",
                    None,
                    "Supervisor started 2 task(s)",
                    "2024-01-01T11:59:00.000Z",
                ),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------


def test_logs_api_redirects_unauthenticated(app: TestClient) -> None:
    """Unauthenticated request to /api/logs should redirect to login."""
    resp = app.get("/api/logs", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_logs_page_redirects_unauthenticated(app: TestClient) -> None:
    """Unauthenticated request to /logs should redirect to login."""
    resp = app.get("/logs", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_logs_partial_redirects_unauthenticated(app: TestClient) -> None:
    """Unauthenticated request to /api/logs/partial should redirect to login."""
    resp = app.get("/api/logs/partial", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


# ---------------------------------------------------------------------------
# GET /api/logs — empty state
# ---------------------------------------------------------------------------


def test_logs_empty_when_no_entries(app: TestClient) -> None:
    """Returns an empty list when search_log has no rows."""
    _login(app)
    resp = app.get("/api/logs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/logs — with seeded data (uses async DB fixture + sync app)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_logs_returns_all_rows(seeded_log: None, async_client: object) -> None:
    """Returns all seeded rows with correct fields when no filter is applied."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    # Setup + login via the async client
    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5

    # Newest first (by timestamp DESC)
    actions = [r["action"] for r in data]
    assert actions[0] == "error"  # 12:03
    assert actions[-1] == "info"  # 11:59


@pytest.mark.asyncio()
async def test_logs_filter_by_instance_id(seeded_log: None, async_client: object) -> None:
    """Filtering by instance_id returns only that instance's rows."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?instance_id=1&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    # instance 1 has 2 rows (101 searched, 102 skipped)
    assert len(data) == 2
    for row in data:
        assert row["instance_id"] == 1


@pytest.mark.asyncio()
async def test_logs_filter_by_action(seeded_log: None, async_client: object) -> None:
    """Filtering by action returns only rows with that action."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?action=searched&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for row in data:
        assert row["action"] == "searched"


@pytest.mark.asyncio()
async def test_logs_limit_restricts_rows(seeded_log: None, async_client: object) -> None:
    """The limit param caps the number of rows returned."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio()
async def test_logs_before_cursor_paginates(seeded_log: None, async_client: object) -> None:
    """The 'before' cursor returns only rows older than the given timestamp."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    # All rows older than 12:02 → should be 12:01, 12:00, 11:59 (3 rows)
    resp = await async_client.get("/api/logs?before=2024-01-01T12:02:00.000Z&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    for row in data:
        assert row["timestamp"] < "2024-01-01T12:02:00.000Z"


# ---------------------------------------------------------------------------
# GET /logs page
# ---------------------------------------------------------------------------


def test_logs_page_renders(app: TestClient) -> None:
    """The /logs page renders 200 OK with the expected HTML structure."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b"Search Logs" in resp.content
    assert b"log-filter-form" in resp.content
    assert b"log-tbody" in resp.content


# ---------------------------------------------------------------------------
# GET /api/logs/partial — HTMX partial
# ---------------------------------------------------------------------------


def test_logs_partial_empty(app: TestClient) -> None:
    """The HTMX partial returns the empty-state row when no logs exist."""
    _login(app)
    resp = app.get("/api/logs/partial")
    assert resp.status_code == 200
    assert b"No log entries found" in resp.content


@pytest.mark.asyncio()
async def test_logs_partial_returns_rows(seeded_log: None, async_client: object) -> None:
    """The HTMX partial contains <tr> elements when rows exist."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=200")
    assert resp.status_code == 200
    content = resp.text
    assert "<tr" in content
    # Should contain action badges
    assert "searched" in content or "skipped" in content

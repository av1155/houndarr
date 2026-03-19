"""Tests for GET /api/status and POST /api/instances/{id}/run-now."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient
from houndarr.database import get_db
from houndarr.engine import supervisor as supervisor_module
from tests.conftest import csrf_headers

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


@pytest.fixture(autouse=True)
def _mock_supervisor_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_op_run_instance_search(*args: object, **kwargs: object) -> int:
        return 0

    monkeypatch.setattr(supervisor_module, "run_instance_search", _no_op_run_instance_search)


def _login(client: TestClient) -> None:
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


async def _seed_status_activity_logs(instance_id: int) -> None:
    """Seed mixed recent/old log actions for status aggregate assertions."""
    now = datetime.now(UTC)
    rows = [
        (
            instance_id,
            101,
            "episode",
            "missing",
            "searched",
            (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        ),
        (
            instance_id,
            102,
            "episode",
            "missing",
            "skipped",
            (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        ),
        (
            instance_id,
            103,
            "episode",
            "missing",
            "error",
            (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        ),
        (
            instance_id,
            104,
            "episode",
            "missing",
            "searched",
            (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        ),
    ]

    async with get_db() as conn:
        await conn.execute("DELETE FROM search_log WHERE instance_id = ?", (instance_id,))
        await conn.executemany(
            """
            INSERT INTO search_log (instance_id, item_id, item_type, search_kind, action, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------


def test_status_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.get("/api/status", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_run_now_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.post("/api/instances/1/run-now", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


# ---------------------------------------------------------------------------
# GET /api/status — no instances
# ---------------------------------------------------------------------------


def test_status_empty_when_no_instances(app: TestClient) -> None:
    _login(app)
    resp = app.get("/api/status")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/status — with instances
# ---------------------------------------------------------------------------


def test_status_returns_correct_shape(app: TestClient) -> None:
    _login(app)
    # Create one instance via the settings UI
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    resp = app.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

    item = data[0]
    assert item["name"] == "My Sonarr"
    assert item["type"] == "sonarr"
    assert item["enabled"] is True
    assert item["last_search_at"] is None
    assert item["searches_last_hour"] == 0
    assert item["searches_today"] == 0
    assert item["items_found_total"] == 0
    assert item["searched_24h"] == 0
    assert item["skipped_24h"] == 0
    assert item["errors_24h"] == 0
    assert item["last_activity_action"] is None
    assert item["last_activity_at"] is None
    assert item["batch_size"] == 2
    assert item["sleep_interval_mins"] == 30
    assert item["hourly_cap"] == 4
    assert item["cooldown_days"] == 14
    assert item["cutoff_enabled"] is False
    assert item["cutoff_batch_size"] == 1
    assert item["post_release_grace_hrs"] == 6


def test_status_returns_multiple_instances(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    app.post(
        "/settings/instances",
        data={**_VALID_FORM, "name": "My Radarr", "type": "radarr", "url": "http://radarr:7878"},
        headers=csrf_headers(app),
    )

    resp = app.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {d["name"] for d in data}
    assert names == {"My Radarr", "My Sonarr"}


def test_status_includes_24h_outcomes_and_last_activity(app: TestClient) -> None:
    _login(app)
    app.post(
        "/settings/instances",
        data={**_VALID_FORM, "name": "Seeded Sonarr"},
        headers=csrf_headers(app),
    )
    created = app.get("/api/status").json()
    inst_id = int(created[0]["id"])
    app.post(f"/settings/instances/{inst_id}/toggle-enabled", headers=csrf_headers(app))
    asyncio.run(_seed_status_activity_logs(inst_id))

    resp = app.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

    item = data[0]
    assert item["name"] == "Seeded Sonarr"
    assert item["searched_24h"] == 1
    assert item["skipped_24h"] == 1
    assert item["errors_24h"] == 1
    assert item["last_activity_action"] == "error"
    assert isinstance(item["last_activity_at"], str)
    assert item["last_search_at"] is not None
    # The only 'searched' within the last hour is at -2h, so last-hour must be 0.
    assert item["searches_last_hour"] == 0


async def _seed_last_hour_regression(instance_id: int) -> None:
    """Seed rows that would expose the old ISO-format comparison bug.

    Before the fix, the ``>=`` comparison between ISO timestamps (``T``
    separator) and ``datetime('now', …)`` results (space separator) was
    purely lexicographic, causing *all* same-UTC-day rows to match.
    """
    now = datetime.now(UTC)
    rows = [
        # Within last hour — should be counted
        (
            instance_id,
            201,
            "episode",
            "missing",
            "searched",
            (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
        # Outside last hour — must NOT be counted
        (
            instance_id,
            202,
            "episode",
            "missing",
            "searched",
            (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
        # Way outside — must NOT be counted
        (
            instance_id,
            203,
            "episode",
            "missing",
            "searched",
            (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
    ]

    async with get_db() as conn:
        await conn.execute("DELETE FROM search_log WHERE instance_id = ?", (instance_id,))
        await conn.executemany(
            """
            INSERT INTO search_log (instance_id, item_id, item_type, search_kind, action, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()


def test_searches_last_hour_excludes_older_rows(app: TestClient) -> None:
    """Regression: searches_last_hour must count only the rolling 60-min window."""
    _login(app)
    app.post(
        "/settings/instances",
        data={**_VALID_FORM, "name": "Hour Regression"},
        headers=csrf_headers(app),
    )
    created = app.get("/api/status").json()
    inst_id = int(created[0]["id"])
    asyncio.run(_seed_last_hour_regression(inst_id))

    resp = app.get("/api/status")
    assert resp.status_code == 200
    item = resp.json()[0]

    # Only 1 of 3 'searched' rows is within the last hour.
    assert item["searches_last_hour"] == 1


async def _seed_today_boundary(instance_id: int) -> None:
    """Seed rows on either side of the UTC midnight boundary."""
    now = datetime.now(UTC)
    # A row clearly in today (UTC)
    today_ts = now.replace(hour=0, minute=5, second=0, microsecond=0)
    # A row clearly in yesterday (UTC)
    yesterday_ts = (now - timedelta(days=1)).replace(
        hour=23,
        minute=55,
        second=0,
        microsecond=0,
    )

    rows = [
        (
            instance_id,
            301,
            "episode",
            "missing",
            "searched",
            today_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
        (
            instance_id,
            302,
            "episode",
            "missing",
            "searched",
            yesterday_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
    ]

    async with get_db() as conn:
        await conn.execute("DELETE FROM search_log WHERE instance_id = ?", (instance_id,))
        await conn.executemany(
            """
            INSERT INTO search_log (instance_id, item_id, item_type, search_kind, action, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()


def test_searches_today_uses_utc_day(app: TestClient) -> None:
    """searches_today counts rows whose date matches the current UTC day."""
    _login(app)
    app.post(
        "/settings/instances",
        data={**_VALID_FORM, "name": "Today Boundary"},
        headers=csrf_headers(app),
    )
    created = app.get("/api/status").json()
    inst_id = int(created[0]["id"])
    asyncio.run(_seed_today_boundary(inst_id))

    resp = app.get("/api/status")
    assert resp.status_code == 200
    item = resp.json()[0]

    # Only the row from today-UTC is counted; yesterday's is excluded.
    assert item["searches_today"] == 1


# ---------------------------------------------------------------------------
# POST /api/instances/{id}/run-now
# ---------------------------------------------------------------------------


@respx.mock
def test_run_now_returns_202(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    # Get the instance id from status
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    # Mock the Sonarr HTTP calls that run-now will trigger in the background
    respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )

    resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["instance_id"] == inst_id


def test_run_now_404_for_unknown_instance(app: TestClient) -> None:
    _login(app)
    resp = app.post("/api/instances/9999/run-now", headers=csrf_headers(app))
    assert resp.status_code == 404


def test_run_now_409_for_disabled_instance(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    app.post(f"/settings/instances/{inst_id}/toggle-enabled", headers=csrf_headers(app))

    resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
    assert resp.status_code == 409

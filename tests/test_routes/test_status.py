"""Tests for GET /api/status and POST /api/instances/{id}/run-now."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

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
    async def _always_ok(self: ArrClient) -> dict[str, Any] | None:
        name = type(self).__name__.replace("Client", "")
        return {"appName": name, "version": "4.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _always_ok)


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
# GET /api/status - no instances
# ---------------------------------------------------------------------------


def test_status_empty_when_no_instances(app: TestClient) -> None:
    _login(app)
    resp = app.get("/api/status")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/status - with instances
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
        # Within last hour - should be counted
        (
            instance_id,
            201,
            "episode",
            "missing",
            "searched",
            (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
        # Outside last hour - must NOT be counted
        (
            instance_id,
            202,
            "episode",
            "missing",
            "searched",
            (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
        # Way outside - must NOT be counted
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


# ---------------------------------------------------------------------------
# GET /api/status?v=2 envelope + redesigned per-instance fields
# ---------------------------------------------------------------------------


def _create_instance(app: TestClient, name: str = "My Sonarr") -> int:
    """Create one instance via the settings route and return its id."""
    form = {**_VALID_FORM, "name": name}
    app.post("/settings/instances", data=form, headers=csrf_headers(app))
    return int(app.get("/api/status").json()[0]["id"])


async def _seed_search_log(rows: list[tuple[Any, ...]]) -> None:
    """Insert raw search_log rows for the redesign fixtures.

    Each tuple: (instance_id, item_id, item_type, search_kind, action,
    reason_or_none, item_label_or_none, message_or_none, timestamp).
    """
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, search_kind, action,
                reason, item_label, message, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()


async def _seed_cooldown(instance_id: int, item_id: int, item_type: str, days_ago: float) -> None:
    when = datetime.now(UTC) - timedelta(days=days_ago)
    iso = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (instance_id, item_id, item_type, iso),
        )
        await conn.commit()


def test_status_v1_unchanged_returns_array(app: TestClient) -> None:
    """Legacy v=1 response stays a plain JSON array."""
    _login(app)
    _create_instance(app)
    body = app.get("/api/status").json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert "instances" not in body[0]


def test_status_v2_envelope_shape(app: TestClient) -> None:
    _login(app)
    _create_instance(app)
    body = app.get("/api/status?v=2").json()
    assert isinstance(body, dict)
    assert set(body.keys()) == {"instances", "recent_searches"}
    assert len(body["instances"]) == 1
    assert isinstance(body["recent_searches"], list)


def test_status_v2_empty_dashboard(app: TestClient) -> None:
    _login(app)
    body = app.get("/api/status?v=2").json()
    assert body == {"instances": [], "recent_searches": []}


def test_status_v2_includes_redesign_fields(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    inst = app.get("/api/status?v=2").json()["instances"][0]
    expected_keys = {
        "lifetime_searched",
        "last_dispatch_at",
        "active_error",
        "cooldown_breakdown",
        "unlocking_next",
        "cooldown_total",
        "monitored_total",
        "unreleased_count",
        "upgrade_enabled",
        "upgrade_cooldown_days",
    }
    assert expected_keys.issubset(inst.keys())
    assert inst["id"] == iid
    assert inst["unreleased_count"] == 0  # PR 1 interim; PR 5 populates real


def test_status_v2_lifetime_searched_counts_all_time(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    now = datetime.now(UTC)
    asyncio.run(
        _seed_search_log(
            [
                (
                    iid,
                    101,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "S01E01",
                    None,
                    (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    102,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "S01E02",
                    None,
                    (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    103,
                    "episode",
                    "missing",
                    "skipped",
                    "on cooldown (14d)",
                    "S01E03",
                    None,
                    (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    inst = app.get("/api/status?v=2").json()["instances"][0]
    assert inst["lifetime_searched"] == 2
    assert inst["last_dispatch_at"] is not None


def test_status_v2_active_error_when_latest_row_is_error(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    now = datetime.now(UTC)
    asyncio.run(
        _seed_search_log(
            [
                (
                    iid,
                    None,
                    None,
                    None,
                    "info",
                    None,
                    None,
                    "cycle complete",
                    (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    None,
                    None,
                    None,
                    "error",
                    None,
                    None,
                    "Could not reach http://sonarr:8989",
                    (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    None,
                    None,
                    None,
                    "error",
                    None,
                    None,
                    "Could not reach http://sonarr:8989",
                    (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    inst = app.get("/api/status?v=2").json()["instances"][0]
    assert inst["active_error"] is not None
    assert inst["active_error"]["failures_count"] == 2
    assert "http://sonarr:8989" in inst["active_error"]["message"]


def test_status_v2_active_error_none_when_latest_row_non_error(app: TestClient) -> None:
    """Banner self-clears as soon as a non-error row lands."""
    _login(app)
    iid = _create_instance(app)
    now = datetime.now(UTC)
    asyncio.run(
        _seed_search_log(
            [
                (
                    iid,
                    None,
                    None,
                    None,
                    "error",
                    None,
                    None,
                    "transient",
                    (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    101,
                    "episode",
                    "missing",
                    "skipped",
                    "on cooldown (14d)",
                    "S01E01",
                    None,
                    (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    inst = app.get("/api/status?v=2").json()["instances"][0]
    assert inst["active_error"] is None


def test_status_v2_recent_searches_last_7_days(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    now = datetime.now(UTC)
    asyncio.run(
        _seed_search_log(
            [
                (
                    iid,
                    101,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "Fresh Show",
                    None,
                    (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    102,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "Older Show",
                    None,
                    (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    103,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "Too Old Show",
                    None,
                    (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    body = app.get("/api/status?v=2").json()
    labels = [row["item_label"] for row in body["recent_searches"]]
    assert labels == ["Fresh Show", "Older Show"]


def test_status_v2_recent_searches_limit_5(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    now = datetime.now(UTC)
    rows = [
        (
            iid,
            100 + i,
            "episode",
            "missing",
            "searched",
            None,
            f"Show {i}",
            None,
            (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        for i in range(10)
    ]
    asyncio.run(_seed_search_log(rows))
    body = app.get("/api/status?v=2").json()
    assert len(body["recent_searches"]) == 5
    # newest first -> item_label "Show 0"
    assert body["recent_searches"][0]["item_label"] == "Show 0"


def test_status_v2_unlocking_next_sorted_by_earliest_unlock(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    # cooldown_days default is 14 for sonarr instance created via /settings
    # earliest unlock = most recently searched (smallest days_ago)
    asyncio.run(_seed_cooldown(iid, 201, "episode", days_ago=13.5))  # unlocks in ~12h
    asyncio.run(_seed_cooldown(iid, 202, "episode", days_ago=10.0))  # unlocks in 4d
    asyncio.run(_seed_cooldown(iid, 203, "episode", days_ago=5.0))  # unlocks in 9d
    asyncio.run(_seed_cooldown(iid, 204, "episode", days_ago=1.0))  # unlocks in 13d
    body = app.get("/api/status?v=2").json()["instances"][0]
    ids = [r["item_id"] for r in body["unlocking_next"]]
    assert ids == [201, 202, 203]
    assert body["cooldown_total"] == 4


def test_status_v2_cooldown_breakdown_splits_by_kind(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    # seed a searched row of each kind per item, then cooldown rows for each
    now = datetime.now(UTC)
    asyncio.run(
        _seed_search_log(
            [
                (
                    iid,
                    301,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "M1",
                    None,
                    (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    302,
                    "episode",
                    "cutoff",
                    "searched",
                    None,
                    "C1",
                    None,
                    (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    303,
                    "episode",
                    "upgrade",
                    "searched",
                    None,
                    "U1",
                    None,
                    (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    304,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "M2",
                    None,
                    (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    asyncio.run(_seed_cooldown(iid, 301, "episode", days_ago=1.0))
    asyncio.run(_seed_cooldown(iid, 302, "episode", days_ago=1.0))
    asyncio.run(_seed_cooldown(iid, 303, "episode", days_ago=1.0))
    asyncio.run(_seed_cooldown(iid, 304, "episode", days_ago=1.0))
    body = app.get("/api/status?v=2").json()["instances"][0]
    assert body["cooldown_breakdown"] == {"missing": 2, "cutoff": 1, "upgrade": 1}


def test_status_v2_monitored_total_reads_app_state(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    snapshots = app.app.state.instance_snapshots
    snapshots[iid] = {"missing_count": 42, "cutoff_count": 8}
    body = app.get("/api/status?v=2").json()["instances"][0]
    assert body["monitored_total"] == 50
    assert body["unreleased_count"] == 0  # PR 1 interim


def test_status_v2_monitored_total_zero_when_no_snapshot(app: TestClient) -> None:
    _login(app)
    _create_instance(app)
    body = app.get("/api/status?v=2").json()["instances"][0]
    assert body["monitored_total"] == 0

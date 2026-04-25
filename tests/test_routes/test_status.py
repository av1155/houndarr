"""Tests for GET /api/status and POST /api/instances/{id}/run-now."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from houndarr.clients._wire_models import SystemStatus
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
    async def _always_ok(self: ArrClient) -> SystemStatus | None:
        name = type(self).__name__.replace("Client", "")
        return SystemStatus(app_name=name, version="4.0.0")

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
    assert resp.json() == {"instances": [], "recent_searches": []}


# ---------------------------------------------------------------------------
# GET /api/status - with instances
# ---------------------------------------------------------------------------


def test_status_returns_correct_shape(app: TestClient) -> None:
    _login(app)
    # Create one instance via the settings UI
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    resp = app.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"instances", "recent_searches"}
    assert len(body["instances"]) == 1

    item = body["instances"][0]
    assert item["name"] == "My Sonarr"
    assert item["type"] == "sonarr"
    assert item["enabled"] is True
    assert item["last_search_at"] is None
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
    instances = resp.json()["instances"]
    assert len(instances) == 2
    names = {d["name"] for d in instances}
    assert names == {"My Radarr", "My Sonarr"}


def test_status_includes_24h_outcomes_and_last_activity(app: TestClient) -> None:
    _login(app)
    app.post(
        "/settings/instances",
        data={**_VALID_FORM, "name": "Seeded Sonarr"},
        headers=csrf_headers(app),
    )
    inst_id = int(app.get("/api/status").json()["instances"][0]["id"])
    app.post(f"/settings/instances/{inst_id}/toggle-enabled", headers=csrf_headers(app))
    asyncio.run(_seed_status_activity_logs(inst_id))

    resp = app.get("/api/status")
    assert resp.status_code == 200
    instances = resp.json()["instances"]
    assert len(instances) == 1

    item = instances[0]
    assert item["name"] == "Seeded Sonarr"
    assert item["searched_24h"] == 1
    assert item["skipped_24h"] == 1
    assert item["errors_24h"] == 1
    assert item["last_activity_action"] == "error"
    assert isinstance(item["last_activity_at"], str)
    assert item["last_search_at"] is not None


# ---------------------------------------------------------------------------
# POST /api/instances/{id}/run-now
# ---------------------------------------------------------------------------


@respx.mock
def test_run_now_returns_202(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    # Get the instance id from status
    inst_id = app.get("/api/status").json()["instances"][0]["id"]

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

    inst_id = app.get("/api/status").json()["instances"][0]["id"]

    app.post(f"/settings/instances/{inst_id}/toggle-enabled", headers=csrf_headers(app))

    resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/status envelope + redesigned per-instance fields
# ---------------------------------------------------------------------------


def _create_instance(app: TestClient, name: str = "My Sonarr") -> int:
    """Create one instance via the settings route and return its id."""
    form = {**_VALID_FORM, "name": name}
    app.post("/settings/instances", data=form, headers=csrf_headers(app))
    return int(app.get("/api/status").json()["instances"][0]["id"])


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


async def _seed_cooldown(
    instance_id: int,
    item_id: int,
    item_type: str,
    days_ago: float,
    search_kind: str = "missing",
) -> None:
    when = datetime.now(UTC) - timedelta(days=days_ago)
    iso = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (instance_id, item_id, item_type, search_kind, iso),
        )
        await conn.commit()


def test_status_envelope_shape(app: TestClient) -> None:
    _login(app)
    _create_instance(app)
    body = app.get("/api/status").json()
    assert isinstance(body, dict)
    assert set(body.keys()) == {"instances", "recent_searches"}
    assert len(body["instances"]) == 1
    assert isinstance(body["recent_searches"], list)


def test_status_v2_includes_redesign_fields(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    inst = app.get("/api/status").json()["instances"][0]
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


def test_status_unreleased_count_zero_on_fresh_db(app: TestClient) -> None:
    """Fresh instance with no snapshot run yet reports unreleased_count=0.

    Pins the post-create / pre-supervisor state.  The supervisor's
    snapshot refresh populates the real value within ~20s of process
    start (or immediately on enable via the prime-on-enable hook).
    """
    _login(app)
    _create_instance(app)
    inst = app.get("/api/status").json()["instances"][0]
    assert inst["unreleased_count"] == 0


def test_status_unreleased_count_reflects_snapshot_update(app: TestClient) -> None:
    """The /api/status envelope echoes whatever the supervisor wrote.

    Direct DB write via update_instance_snapshot stands in for a
    real supervisor refresh; the route is a passive read of the
    stored snapshot column.
    """
    from houndarr.repositories.instances import update_instance_snapshot

    _login(app)
    iid = _create_instance(app)
    asyncio.run(update_instance_snapshot(iid, monitored_total=42, unreleased_count=7))

    inst = app.get("/api/status").json()["instances"][0]
    assert inst["id"] == iid
    assert inst["monitored_total"] == 42
    assert inst["unreleased_count"] == 7


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
    inst = app.get("/api/status").json()["instances"][0]
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
    inst = app.get("/api/status").json()["instances"][0]
    assert inst["active_error"] is not None
    assert inst["active_error"]["failures_count"] == 2
    assert "http://sonarr:8989" in inst["active_error"]["message"]


def test_status_active_error_still_emitted_for_disabled_instance(app: TestClient) -> None:
    """API reflects raw DB state: a disabled instance with an error row
    keeps its ``active_error`` in the response.  The dashboard suppresses
    the banner client-side (dashboard_content.html renderAlert + the
    subheader sentence both filter on ``enabled && active_error``), so
    asserting the API shape here documents the backend contract and
    keeps the two layers independently correct.
    """
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
                    "Could not reach http://sonarr:8989",
                    (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    app.post(f"/settings/instances/{iid}/toggle-enabled", headers=csrf_headers(app))
    inst = app.get("/api/status").json()["instances"][0]
    assert inst["enabled"] is False
    assert inst["active_error"] is not None
    assert "http://sonarr:8989" in inst["active_error"]["message"]


def test_dashboard_template_filters_banner_on_disabled_instances() -> None:
    """Regression guard for the client-side alert filter.

    ``renderAlert`` and ``renderSubheader`` in ``dashboard.js`` must
    both route through ``failingInstances(instances)`` so disabling an
    instance silences the top-of-dashboard banner AND the "needs
    attention" callout through a single source of truth.  The shared
    helper pins the ``enabled && active_error`` predicate in one place
    so a new surface that needs the same filter cannot accidentally
    drift from the banner's rules.
    """
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2] / "src" / "houndarr" / "static" / "js" / "dashboard.js"
    ).read_text()
    # Exactly one predicate: the helper is the only place the filter
    # lives.  A second occurrence would mean someone re-inlined the
    # check instead of calling failingInstances().
    assert script.count("i.enabled && i.active_error") == 1, (
        "Expected exactly one enabled+active_error predicate (inside failingInstances)"
    )
    # Helper must be called from both the banner and the subheader so
    # the two surfaces agree on which instances are failing right now.
    assert script.count("failingInstances(instances)") >= 2, (
        "Expected failingInstances(instances) to be called from both "
        "renderAlert and renderSubheader"
    )


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
    inst = app.get("/api/status").json()["instances"][0]
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
    body = app.get("/api/status").json()
    labels = [row["item_label"] for row in body["recent_searches"]]
    assert labels == ["Fresh Show", "Older Show"]


def test_status_v2_envelope_includes_budget_bar_fields(app: TestClient) -> None:
    """Each instance carries the data the hourly budget bar needs.

    `searches_last_hour` is the numerator (rolling 1-hour SUM of
    dispatches).  `cutoff_hourly_cap` and `upgrade_hourly_cap` let
    the dashboard sum the dominant-cap denominator without hitting
    the DB again.
    """
    _login(app)
    _create_instance(app)
    body = app.get("/api/status").json()
    row = body["instances"][0]
    assert "searches_last_hour" in row
    assert "cutoff_hourly_cap" in row
    assert "upgrade_hourly_cap" in row
    assert isinstance(row["searches_last_hour"], int)
    assert isinstance(row["cutoff_hourly_cap"], int)
    assert isinstance(row["upgrade_hourly_cap"], int)


def test_status_v2_searches_last_hour_counts_only_recent(app: TestClient) -> None:
    """The budget counter windows strictly to the last 60 minutes."""
    _login(app)
    iid = _create_instance(app)
    now = datetime.now(UTC)
    asyncio.run(
        _seed_search_log(
            [
                (
                    iid,
                    201,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "Fresh",
                    None,
                    (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    202,
                    "episode",
                    "cutoff",
                    "searched",
                    None,
                    "Also Fresh",
                    None,
                    (now - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    203,
                    "episode",
                    "missing",
                    "searched",
                    None,
                    "Just Outside",
                    None,
                    (now - timedelta(minutes=65)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    body = app.get("/api/status").json()
    row = body["instances"][0]
    # Two rows are inside the 60-minute window (5m and 45m); the 65m row is out.
    assert row["searches_last_hour"] == 2


def test_status_v2_recent_searches_includes_search_kind(app: TestClient) -> None:
    """Each recent-hunts row surfaces its pass kind so the dashboard can icon-tag it."""
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
                    "Missing Ep",
                    None,
                    (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    102,
                    "episode",
                    "cutoff",
                    "searched",
                    None,
                    "Cutoff Ep",
                    None,
                    (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
                (
                    iid,
                    103,
                    "episode",
                    "upgrade",
                    "searched",
                    None,
                    "Upgrade Ep",
                    None,
                    (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                ),
            ]
        )
    )
    body = app.get("/api/status").json()
    rows = body["recent_searches"]
    # Newest-first ordering preserved, each row carries its search_kind.
    assert [(r["item_label"], r["search_kind"]) for r in rows] == [
        ("Missing Ep", "missing"),
        ("Cutoff Ep", "cutoff"),
        ("Upgrade Ep", "upgrade"),
    ]


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
    body = app.get("/api/status").json()
    assert len(body["recent_searches"]) == 5
    # newest first -> item_label "Show 0"
    assert body["recent_searches"][0]["item_label"] == "Show 0"


def test_status_v2_unlocking_next_spread_across_schedule(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    # cooldown_days default is 14 for sonarr instance created via /settings
    # earliest unlock = most recently searched (smallest days_ago)
    asyncio.run(_seed_cooldown(iid, 201, "episode", days_ago=13.5))  # unlocks in ~12h
    asyncio.run(_seed_cooldown(iid, 202, "episode", days_ago=10.0))  # unlocks in 4d
    asyncio.run(_seed_cooldown(iid, 203, "episode", days_ago=5.0))  # unlocks in 9d
    asyncio.run(_seed_cooldown(iid, 204, "episode", days_ago=1.0))  # unlocks in 13d
    body = app.get("/api/status").json()["instances"][0]
    ids = [r["item_id"] for r in body["unlocking_next"]]
    # Spread picks: sorted ascending → [201, 202, 203, 204]; indices
    # [0, n//2, n-1] = [0, 2, 3] → ids [201, 203, 204]. The median slot
    # (203) replaces the second-soonest so the three rows never collapse
    # to a single batch's clone-unlock timestamps.
    assert ids == [201, 203, 204]
    assert body["cooldown_total"] == 4


def test_status_v2_unlocking_next_returns_all_when_three_or_fewer(app: TestClient) -> None:
    _login(app)
    iid = _create_instance(app)
    asyncio.run(_seed_cooldown(iid, 301, "episode", days_ago=13.0))
    asyncio.run(_seed_cooldown(iid, 302, "episode", days_ago=10.0))
    body = app.get("/api/status").json()["instances"][0]
    ids = [r["item_id"] for r in body["unlocking_next"]]
    assert ids == [301, 302]


def test_status_v2_unlocking_next_keys_window_by_kind(app: TestClient) -> None:
    """Per-kind unlock windows: an upgrade-kind cooldown row renders the
    upgrade_cooldown_days (default 90) rather than collapsing to the
    min-across-enabled cooldown_days (default 14).  Regression for
    `_cooldown_data` previously using a single ``min_days`` for all rows.
    """
    _login(app)
    iid = _create_instance(app)

    # Enable cutoff + upgrade on the seeded instance so their windows matter.
    async def _enable_passes() -> None:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE instances SET cutoff_enabled = 1, upgrade_enabled = 1 WHERE id = ?",
                (iid,),
            )
            await conn.commit()

    asyncio.run(_enable_passes())
    now = datetime.now(UTC)
    searched_at = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # Seed three rows (one per kind) at the same searched_at so only the
    # per-row cooldown window explains the different unlock times.
    asyncio.run(
        _seed_search_log(
            [
                (iid, 501, "episode", "missing", "searched", None, "M", None, searched_at),
                (iid, 502, "episode", "cutoff", "searched", None, "C", None, searched_at),
                (iid, 503, "episode", "upgrade", "searched", None, "U", None, searched_at),
            ]
        )
    )
    asyncio.run(_seed_cooldown(iid, 501, "episode", days_ago=1.0, search_kind="missing"))
    asyncio.run(_seed_cooldown(iid, 502, "episode", days_ago=1.0, search_kind="cutoff"))
    asyncio.run(_seed_cooldown(iid, 503, "episode", days_ago=1.0, search_kind="upgrade"))

    body = app.get("/api/status").json()["instances"][0]
    picks = {row["item_id"]: row for row in body["unlocking_next"]}
    assert set(picks) == {501, 502, 503}

    def _days_from_now(iso: str) -> float:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (parsed - now).total_seconds() / 86400.0

    # Defaults (see config.py): cooldown_days=14, cutoff_cooldown_days=21,
    # upgrade_cooldown_days=90.  Each row was searched 1 day ago, so the
    # remaining window is N-1 days.
    assert 12.5 < _days_from_now(picks[501]["unlock_at"]) < 13.5  # missing: 14 - 1
    assert 19.5 < _days_from_now(picks[502]["unlock_at"]) < 20.5  # cutoff: 21 - 1
    assert 88.5 < _days_from_now(picks[503]["unlock_at"]) < 89.5  # upgrade: 90 - 1


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
    asyncio.run(_seed_cooldown(iid, 301, "episode", days_ago=1.0, search_kind="missing"))
    asyncio.run(_seed_cooldown(iid, 302, "episode", days_ago=1.0, search_kind="cutoff"))
    asyncio.run(_seed_cooldown(iid, 303, "episode", days_ago=1.0, search_kind="upgrade"))
    asyncio.run(_seed_cooldown(iid, 304, "episode", days_ago=1.0, search_kind="missing"))
    body = app.get("/api/status").json()["instances"][0]
    assert body["cooldown_breakdown"] == {"missing": 2, "cutoff": 1, "upgrade": 1}


def test_status_monitored_total_reads_column(app: TestClient, async_client: object) -> None:  # noqa: ARG001
    """monitored_total reflects the authoritative column written by the
    supervisor's snapshot refresh task."""
    from houndarr.services.instances import update_instance_snapshot

    _login(app)
    iid = _create_instance(app)
    asyncio.run(update_instance_snapshot(iid, monitored_total=42, unreleased_count=5))
    body = app.get("/api/status").json()["instances"][0]
    assert body["monitored_total"] == 42
    assert body["unreleased_count"] == 5


def test_status_monitored_total_zero_when_no_snapshot(app: TestClient) -> None:
    _login(app)
    _create_instance(app)
    body = app.get("/api/status").json()["instances"][0]
    assert body["monitored_total"] == 0
    assert body["unreleased_count"] == 0


def test_status_reconciled_invariant_holds(app: TestClient) -> None:
    """After reconciliation, per-instance cooldown counts must not
    exceed monitored_total.

    Encodes the invariant the dashboard's Eligible formula depends on:
    ``monitored_total >= missing_cd + cutoff_cd`` for every enabled
    instance in /api/status.  Pre-reconcile this could fail (downloads
    / unmonitors left stale cooldown rows behind); post-reconcile the
    cooldowns table is a projection of the *arr's live state, so the
    inequality holds by construction.  The seed below mirrors that
    post-reconcile steady state: cooldowns only exist for items still
    present in monitored_total.
    """
    from houndarr.services.instances import update_instance_snapshot

    _login(app)
    iid = _create_instance(app)
    asyncio.run(update_instance_snapshot(iid, monitored_total=10, unreleased_count=2))
    # Seed a realistic post-reconcile mix: six cooldowns against
    # ten monitored items.  missing_cd + cutoff_cd = 6 <= 10.
    for i in range(1, 5):
        asyncio.run(_seed_cooldown(iid, i, "episode", days_ago=1.0, search_kind="missing"))
    for i in range(100, 102):
        asyncio.run(_seed_cooldown(iid, i, "episode", days_ago=1.0, search_kind="cutoff"))

    body = app.get("/api/status").json()["instances"][0]
    bd = body["cooldown_breakdown"]
    monitored = body["monitored_total"]
    unreleased = body["unreleased_count"]
    gated = bd["missing"] + bd["cutoff"]
    assert gated + unreleased <= monitored, (
        f"invariant violated: gated={gated} unreleased={unreleased} "
        f"monitored={monitored} breakdown={bd}"
    )

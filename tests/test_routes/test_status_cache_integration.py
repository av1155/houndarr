"""Integration tests for the dashboard aggregate cache through the HTTP layer.

The autouse ``_disable_dashboard_cache`` fixture in ``tests/conftest.py``
patches ``DASHBOARD_CACHE_TTL_SECONDS`` to ``0`` so legacy tests run
against an uncached route.  This module overrides that fixture to drive
the production cache path through ``TestClient`` end-to-end and verify
the wiring the audit flagged as unverified at the HTTP boundary.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from houndarr.clients._wire_models import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.database import get_db
from houndarr.engine import supervisor as supervisor_module
from tests.conftest import csrf_headers


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the *arr ``ping`` so instance creation passes the connection test."""

    async def _always_ok(self: ArrClient) -> SystemStatus | None:
        name = type(self).__name__.replace("Client", "")
        return SystemStatus(app_name=name, version="4.0.0")

    monkeypatch.setattr(ArrClient, "ping", _always_ok)


@pytest.fixture(autouse=True)
def _mock_supervisor_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the supervisor's per-cycle search call so the loop is a no-op."""

    async def _no_op(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(supervisor_module, "run_instance_search", _no_op)


@pytest.fixture(autouse=True)
def _enable_dashboard_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-enable the cache for this module by lifting conftest's patch.

    The autouse ``_disable_dashboard_cache`` fixture in conftest patches
    ``DASHBOARD_CACHE_TTL_SECONDS`` to ``0``; pytest-applied autouse
    fixtures stack, and the LATER monkeypatch wins.  Setting the value
    to a generous TTL ensures the cache survives across the
    HTTP-level operations these tests perform.
    """
    import houndarr.services.metrics as _metrics

    monkeypatch.setattr(_metrics, "DASHBOARD_CACHE_TTL_SECONDS", 30)


def _login(client: TestClient) -> None:
    """Walk the setup + login flow so authenticated routes are reachable."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


def _create_instance(client: TestClient, name: str = "Cache Sonarr") -> int:
    """Create one instance via the settings route and return its id."""
    form = {
        "name": name,
        "type": "sonarr",
        "url": "http://sonarr:8989",
        "api_key": "test-api-key",
        "connection_verified": "true",
    }
    client.post("/settings/instances", data=form, headers=csrf_headers(client))
    resp = client.get("/api/status")
    assert resp.status_code == 200
    return int(resp.json()["instances"][0]["id"])


async def _insert_search_log_row(instance_id: int, label: str) -> None:
    """Insert one ``searched`` row directly via SQL, bypassing every route.

    Direct DB writes do not call ``invalidate_dashboard_cache``; that is
    the whole point of these tests.  A subsequent ``/api/status`` poll
    should serve the *previous* cached envelope until either the TTL
    expires or a mutation route invalidates the cache.
    """
    now = datetime.now(UTC)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, item_id, item_type, search_kind,"
            " action, item_label, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                instance_id,
                999,
                "episode",
                "missing",
                "searched",
                label,
                (now - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            ),
        )
        await conn.commit()


@pytest.mark.asyncio()
async def test_cache_serves_stale_data_when_db_changes_outside_routes(
    app: TestClient,
) -> None:
    """A direct SQL write between two polls is hidden by the cache.

    Pins the production contract: only mutation routes that fan out to
    ``invalidate_dashboard_cache`` cause a fresh DB scan.  Background
    cycle work (which writes to ``search_log`` on every cycle) is
    absorbed by the TTL, not by per-write invalidation.
    """
    _login(app)
    iid = _create_instance(app, "Cache Sonarr A")

    # Warm the cache: this poll loads the (empty) recent_searches.
    first = app.get("/api/status").json()
    assert first["recent_searches"] == []

    # Background-style write: bypass the route, mimicking the supervisor.
    await _insert_search_log_row(iid, "Sneaked In")

    # Cached envelope still wins; the background write is invisible
    # until the TTL expires or a mutation route invalidates.
    second = app.get("/api/status").json()
    assert second["recent_searches"] == []


@pytest.mark.asyncio()
async def test_clear_logs_route_invalidates_cache(app: TestClient) -> None:
    """``POST /settings/admin/clear-logs`` must drop the cache.

    Pins the invalidation hook in :mod:`houndarr.routes.admin`.  Without
    it, the dashboard's ``recent_searches`` strip would still show
    pre-clear rows for up to the cache TTL after the operator clicked
    the maintenance button.
    """
    _login(app)
    iid = _create_instance(app, "Cache Sonarr B")

    # Seed one row, warm the cache so it is reflected, then clear logs
    # and confirm the next poll returns an empty recent_searches strip.
    await _insert_search_log_row(iid, "Pre-clear row")
    app.post("/settings/admin/clear-logs", headers=csrf_headers(app))

    after_clear = app.get("/api/status").json()
    # ``clear_all_search_logs`` writes a single audit breadcrumb at
    # info level, so recent_searches (which filters on action='searched')
    # should be empty.  If the cache had leaked the pre-clear row, the
    # length would be 1.
    assert after_clear["recent_searches"] == []


@pytest.mark.asyncio()
async def test_run_now_schedules_deferred_reinvalidation(
    app: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``POST /api/instances/{id}/run-now`` re-invalidates after the delay.

    Pins the deferred ``asyncio.create_task`` that reclears the cache
    once the supervisor's background search-log write should have
    landed.  Reduces the delay constant so the test does not rely on
    real-time waits.
    """
    import houndarr.routes.api.status as status_module

    monkeypatch.setattr(status_module, "_RUN_NOW_REINVALIDATE_DELAY_SECONDS", 0.05)

    _login(app)
    _create_instance(app, "Cache Sonarr C")

    # Seed app.state with a fake cache that records cache_clear calls.
    clear_count = {"n": 0}

    class _RecordingCache:
        def cache_clear(self) -> None:
            clear_count["n"] += 1

        async def __call__(self, _ids: tuple[int, ...]) -> object:
            from houndarr.services.metrics import DashboardAggregates

            return DashboardAggregates()

    app.app.state.aggregate_cache = _RecordingCache()

    resp = app.post("/api/instances/1/run-now", headers=csrf_headers(app))
    assert resp.status_code == 202

    # Synchronous invalidate: 1 clear right now.
    assert clear_count["n"] == 1

    # Wait for the deferred reinvalidation to fire.
    await asyncio.sleep(0.2)
    assert clear_count["n"] == 2

"""Tests for GET /api/status and POST /api/instances/{id}/run-now."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient

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
    app.post("/settings/instances", data=_VALID_FORM)

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


def test_status_returns_multiple_instances(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)
    app.post(
        "/settings/instances",
        data={**_VALID_FORM, "name": "My Radarr", "type": "radarr", "url": "http://radarr:7878"},
    )

    resp = app.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {d["name"] for d in data}
    assert names == {"My Sonarr", "My Radarr"}


# ---------------------------------------------------------------------------
# POST /api/instances/{id}/run-now
# ---------------------------------------------------------------------------


@respx.mock
def test_run_now_returns_202(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)

    # Get the instance id from status
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    # Mock the Sonarr HTTP calls that run-now will trigger in the background
    respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )

    resp = app.post(f"/api/instances/{inst_id}/run-now")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["instance_id"] == inst_id


def test_run_now_404_for_unknown_instance(app: TestClient) -> None:
    _login(app)
    resp = app.post("/api/instances/9999/run-now")
    assert resp.status_code == 404

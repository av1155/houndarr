"""Tests for POST /api/instances/{id}/run-now edge cases.

Complements the basic run-now assertions in test_status.py with supervisor
availability, CSRF enforcement, response body shape, and path-param validation.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient
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


# ---------------------------------------------------------------------------
# Supervisor availability (503)
# ---------------------------------------------------------------------------


def test_run_now_503_when_supervisor_missing(app: TestClient) -> None:
    """Returns 503 when app.state has no supervisor attribute."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    # Remove the supervisor from app state
    original = getattr(app.app.state, "supervisor", None)  # type: ignore[union-attr]
    try:
        app.app.state.supervisor = None  # type: ignore[union-attr]
        resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Supervisor unavailable"
    finally:
        app.app.state.supervisor = original  # type: ignore[union-attr]


def test_run_now_503_when_supervisor_wrong_type(app: TestClient) -> None:
    """Returns 503 when supervisor is set but not a Supervisor instance."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    original = getattr(app.app.state, "supervisor", None)  # type: ignore[union-attr]
    try:
        app.app.state.supervisor = "not-a-supervisor"  # type: ignore[union-attr]
        resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
        assert resp.status_code == 503
    finally:
        app.app.state.supervisor = original  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# CSRF enforcement
# ---------------------------------------------------------------------------


def test_run_now_requires_csrf_token(app: TestClient) -> None:
    """POST /api/instances/{id}/run-now without CSRF token is rejected."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    # POST without CSRF headers
    resp = app.post(f"/api/instances/{inst_id}/run-now")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Response body shape
# ---------------------------------------------------------------------------


@respx.mock
def test_run_now_202_response_body_shape(app: TestClient) -> None:
    """202 response body contains exactly 'status' and 'instance_id' keys."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )

    resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
    assert resp.status_code == 202
    body = resp.json()
    assert set(body.keys()) == {"status", "instance_id"}
    assert body["status"] == "accepted"
    assert isinstance(body["instance_id"], int)
    assert body["instance_id"] == inst_id


def test_run_now_404_response_body(app: TestClient) -> None:
    """404 response includes a descriptive detail field."""
    _login(app)
    resp = app.post("/api/instances/9999/run-now", headers=csrf_headers(app))
    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body
    assert "not found" in body["detail"].lower()


def test_run_now_409_response_body(app: TestClient) -> None:
    """409 response includes a descriptive detail field."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    status = app.get("/api/status").json()
    inst_id = status[0]["id"]

    # Disable the instance
    app.post(f"/settings/instances/{inst_id}/toggle-enabled", headers=csrf_headers(app))

    resp = app.post(f"/api/instances/{inst_id}/run-now", headers=csrf_headers(app))
    assert resp.status_code == 409
    body = resp.json()
    assert "detail" in body
    assert "disabled" in body["detail"].lower()


# ---------------------------------------------------------------------------
# Path parameter validation
# ---------------------------------------------------------------------------


def test_run_now_non_integer_id_returns_422(app: TestClient) -> None:
    """Non-integer instance_id in the URL path returns 422."""
    _login(app)
    resp = app.post("/api/instances/abc/run-now", headers=csrf_headers(app))
    assert resp.status_code == 422

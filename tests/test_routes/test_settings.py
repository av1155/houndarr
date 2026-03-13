"""Tests for the settings page routes (instance management)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_FORM = {
    "name": "My Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "api_key": "test-api-key-abc123",
    "connection_verified": "true",
}


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _always_true(self: ArrClient) -> bool:
        return True

    monkeypatch.setattr(ArrClient, "ping", _always_true)


def _login(client: TestClient) -> None:
    """Complete setup + login so subsequent requests are authenticated."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


# ---------------------------------------------------------------------------
# Authentication guard — all settings routes require a session
# ---------------------------------------------------------------------------


_AUTH_LOCATIONS = {"/setup", "/login", "http://testserver/setup", "http://testserver/login"}


def test_settings_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.get("/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_settings_create_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.post("/settings/instances", data=_VALID_FORM, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_settings_edit_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.get("/settings/instances/1/edit", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_settings_update_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.post("/settings/instances/1", data=_VALID_FORM, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_settings_delete_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.delete("/settings/instances/1", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_settings_toggle_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.post("/settings/instances/1/toggle-enabled", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------


def test_settings_page_renders(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings")
    assert resp.status_code == 200
    assert b"Settings" in resp.content
    assert b"Instances" in resp.content


def test_settings_page_shows_no_instances_message(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings")
    assert resp.status_code == 200
    assert b"No instances configured" in resp.content


def test_settings_page_includes_add_instance_modal(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings")
    assert resp.status_code == 200
    assert b"add-instance-modal" in resp.content
    assert b"add-instance-modal-content" in resp.content


# ---------------------------------------------------------------------------
# POST /settings/instances (create)
# ---------------------------------------------------------------------------


def test_create_instance_returns_table_body(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances", data=_VALID_FORM)
    assert resp.status_code == 200
    assert b"My Sonarr" in resp.content


def test_create_instance_sonarr_badge(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances", data=_VALID_FORM)
    assert b"Sonarr" in resp.content


def test_create_instance_radarr(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "name": "My Radarr", "type": "radarr", "url": "http://radarr:7878"}
    resp = app.post("/settings/instances", data=form)
    assert resp.status_code == 200
    assert b"My Radarr" in resp.content
    assert b"Radarr" in resp.content


def test_create_instance_invalid_type_returns_422(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "type": "plex"}
    resp = app.post("/settings/instances", data=form)
    assert resp.status_code == 422


def test_create_instance_requires_successful_test(app: TestClient) -> None:
    _login(app)
    form = {k: v for k, v in _VALID_FORM.items() if k != "connection_verified"}
    resp = app.post("/settings/instances", data=form)
    assert resp.status_code == 422
    assert b"Test connection successfully before adding" in resp.content


def test_create_instance_missing_name_returns_422(app: TestClient) -> None:
    _login(app)
    form = {k: v for k, v in _VALID_FORM.items() if k != "name"}
    resp = app.post("/settings/instances", data=form)
    assert resp.status_code == 422


def test_create_instance_missing_api_key_returns_422(app: TestClient) -> None:
    _login(app)
    form = {k: v for k, v in _VALID_FORM.items() if k != "api_key"}
    resp = app.post("/settings/instances", data=form)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /settings/instances/{id}/edit
# ---------------------------------------------------------------------------


def test_edit_form_returns_partial(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)
    resp = app.get("/settings/instances/1/edit")
    assert resp.status_code == 200
    assert b"My Sonarr" in resp.content
    assert b"Save Changes" in resp.content
    assert b"<tr" not in resp.content
    assert b'data-form-mode="edit"' in resp.content


def test_edit_form_not_found(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/instances/9999/edit")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /settings/instances/{id} (update)
# ---------------------------------------------------------------------------


def test_update_instance(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)
    updated_form = {**_VALID_FORM, "name": "Renamed Sonarr"}
    resp = app.post("/settings/instances/1", data=updated_form)
    assert resp.status_code == 200
    assert b"Renamed Sonarr" in resp.content


def test_update_instance_requires_successful_test(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)
    updated_form = {k: v for k, v in _VALID_FORM.items() if k != "connection_verified"}
    resp = app.post("/settings/instances/1", data=updated_form)
    assert resp.status_code == 422
    assert b"Test connection successfully before saving changes" in resp.content


def test_update_instance_not_found(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances/9999", data=_VALID_FORM)
    assert resp.status_code == 404


def test_toggle_instance_enabled(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)

    first = app.post("/settings/instances/1/toggle-enabled")
    assert first.status_code == 200
    assert b"Enable" in first.content
    assert b"Search disabled" in first.content

    second = app.post("/settings/instances/1/toggle-enabled")
    assert second.status_code == 200
    assert b"Disable" in second.content
    assert b"Search enabled" in second.content


def test_toggle_instance_not_found(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances/9999/toggle-enabled")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /settings/instances/{id}
# ---------------------------------------------------------------------------


def test_delete_instance_returns_200(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)
    resp = app.delete("/settings/instances/1")
    assert resp.status_code == 200


def test_delete_instance_gone_from_settings(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM)
    app.delete("/settings/instances/1")
    resp = app.get("/settings")
    assert resp.status_code == 200
    assert b"No instances configured" in resp.content


def test_delete_nonexistent_returns_200(app: TestClient) -> None:
    """Deleting a non-existent ID is idempotent — still 200."""
    _login(app)
    resp = app.delete("/settings/instances/9999")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /settings/instances/add-form
# ---------------------------------------------------------------------------


def test_add_form_partial_renders(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/instances/add-form")
    assert resp.status_code == 200
    assert b"Add Instance" in resp.content


def test_connection_check_endpoint_success(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "sonarr", "url": "http://sonarr:8989", "api_key": "abc"},
    )
    assert resp.status_code == 200
    assert b"Connection successful" in resp.content


def test_connection_check_endpoint_invalid_type(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "plex", "url": "http://sonarr:8989", "api_key": "abc"},
    )
    assert resp.status_code == 422
    assert b"Invalid type" in resp.content

"""Tests for the settings page routes (instance management)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient
from tests.conftest import csrf_headers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_FORM = {
    "name": "My Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "api_key": "test-api-key-abc123",
    "sonarr_search_mode": "episode",
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


def test_password_change_redirects_unauthenticated(app: TestClient) -> None:
    resp = app.post(
        "/settings/account/password",
        data={
            "current_password": "ValidPass1!",
            "new_password": "NewValidPass2!",
            "new_password_confirm": "NewValidPass2!",
        },
        follow_redirects=False,
    )
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
    assert b"https://github.com/av1155/houndarr" in resp.content
    assert b"Settings Guide" in resp.content
    assert b'href="/settings/help"' in resp.content
    assert b"Account" in resp.content
    assert b"Update Password" in resp.content
    assert b"Signed in as" in resp.content
    assert b'id="account-settings"' in resp.content
    assert b'id="account-settings" open' not in resp.content
    assert b"What do these settings mean?" in resp.content


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


def test_settings_help_page_renders(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/help")
    assert resp.status_code == 200
    assert b"Instance Settings Help" in resp.content
    assert b"https://github.com/av1155/houndarr/blob/main/docs/settings.md" in resp.content


# ---------------------------------------------------------------------------
# POST /settings/instances (create)
# ---------------------------------------------------------------------------


def test_create_instance_returns_table_body(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    assert resp.status_code == 200
    assert b"My Sonarr" in resp.content


def test_create_instance_sonarr_badge(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    assert b"Sonarr" in resp.content


def test_create_instance_radarr(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "name": "My Radarr", "type": "radarr", "url": "http://radarr:7878"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 200
    assert b"My Radarr" in resp.content
    assert b"Radarr" in resp.content


def test_create_instance_defaults_to_enabled(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    assert resp.status_code == 200
    assert b"Search enabled" in resp.content


def test_create_instance_invalid_type_returns_422(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "type": "plex"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422


def test_create_instance_requires_successful_test(app: TestClient) -> None:
    _login(app)
    form = {k: v for k, v in _VALID_FORM.items() if k != "connection_verified"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Test connection successfully before adding" in resp.content


def test_create_instance_missing_name_returns_422(app: TestClient) -> None:
    _login(app)
    form = {k: v for k, v in _VALID_FORM.items() if k != "name"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422


def test_create_instance_missing_api_key_returns_422(app: TestClient) -> None:
    _login(app)
    form = {k: v for k, v in _VALID_FORM.items() if k != "api_key"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /settings/instances/{id}/edit
# ---------------------------------------------------------------------------


def test_edit_form_returns_partial(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    resp = app.get("/settings/instances/1/edit")
    assert resp.status_code == 200
    assert b"My Sonarr" in resp.content
    assert b"Save Changes" in resp.content
    assert b"<tr" not in resp.content
    assert b'data-form-mode="edit"' in resp.content
    assert b'name="enabled"' not in resp.content
    # API key field must not contain the real key — only the sentinel
    assert b"__UNCHANGED__" in resp.content


def test_edit_form_not_found(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/instances/9999/edit")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /settings/instances/{id} (update)
# ---------------------------------------------------------------------------


def test_update_instance(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    updated_form = {**_VALID_FORM, "name": "Renamed Sonarr"}
    resp = app.post("/settings/instances/1", data=updated_form, headers=csrf_headers(app))
    assert resp.status_code == 200
    assert b"Renamed Sonarr" in resp.content


def test_update_instance_with_unchanged_api_key(app: TestClient) -> None:
    """Submitting the sentinel key value should preserve the existing stored key."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    # Use the sentinel value — server must keep the original key
    sentinel_form = {**_VALID_FORM, "name": "Renamed Sonarr", "api_key": "__UNCHANGED__"}
    resp = app.post("/settings/instances/1", data=sentinel_form, headers=csrf_headers(app))
    assert resp.status_code == 200
    assert b"Renamed Sonarr" in resp.content


def test_update_instance_preserves_enabled_state(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    app.post("/settings/instances/1/toggle-enabled", headers=csrf_headers(app))

    updated_form = {**_VALID_FORM, "name": "Still Disabled"}
    resp = app.post("/settings/instances/1", data=updated_form, headers=csrf_headers(app))

    assert resp.status_code == 200
    assert b"Still Disabled" in resp.content
    assert b"Search disabled" in resp.content
    assert b"Enable" in resp.content


def test_update_instance_requires_successful_test(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    updated_form = {k: v for k, v in _VALID_FORM.items() if k != "connection_verified"}
    resp = app.post("/settings/instances/1", data=updated_form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Test connection successfully before saving changes" in resp.content


def test_update_instance_not_found(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances/9999", data=_VALID_FORM, headers=csrf_headers(app))
    assert resp.status_code == 404


def test_toggle_instance_enabled(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    first = app.post("/settings/instances/1/toggle-enabled", headers=csrf_headers(app))
    assert first.status_code == 200
    assert b"Enable" in first.content
    assert b"Search disabled" in first.content

    second = app.post("/settings/instances/1/toggle-enabled", headers=csrf_headers(app))
    assert second.status_code == 200
    assert b"Disable" in second.content
    assert b"Search enabled" in second.content


def test_toggle_instance_not_found(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances/9999/toggle-enabled", headers=csrf_headers(app))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /settings/instances/{id}
# ---------------------------------------------------------------------------


def test_delete_instance_returns_200(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    resp = app.delete("/settings/instances/1", headers=csrf_headers(app))
    assert resp.status_code == 200


def test_delete_instance_gone_from_settings(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    app.delete("/settings/instances/1", headers=csrf_headers(app))
    resp = app.get("/settings")
    assert resp.status_code == 200
    assert b"No instances configured" in resp.content


def test_delete_nonexistent_returns_200(app: TestClient) -> None:
    """Deleting a non-existent ID is idempotent — still 200."""
    _login(app)
    resp = app.delete("/settings/instances/9999", headers=csrf_headers(app))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /settings/instances/add-form
# ---------------------------------------------------------------------------


def test_add_form_partial_renders(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/instances/add-form")
    assert resp.status_code == 200
    assert b"Add Instance" in resp.content
    assert b'name="enabled"' not in resp.content
    assert b'name="cutoff_cooldown_days"' in resp.content
    assert b'name="cutoff_hourly_cap"' in resp.content
    assert b'name="batch_size" type="number" min="1" max="250"' in resp.content
    assert b'value="2"' in resp.content
    assert b'name="sleep_interval_mins" type="number" min="1"' in resp.content
    assert b'value="30"' in resp.content
    assert b'name="hourly_cap" type="number" min="0"' in resp.content
    assert b'value="4"' in resp.content
    assert b'name="cooldown_days" type="number" min="0"' in resp.content
    assert b'value="14"' in resp.content
    assert b'name="unreleased_delay_hrs" type="number" min="0"' in resp.content
    assert b'value="36"' in resp.content
    assert b'name="sonarr_search_mode"' in resp.content
    assert b"Season-context search (advanced)" in resp.content
    assert b'href="/settings/help"' not in resp.content
    assert b'target="_blank"' not in resp.content


def test_create_instance_stores_sonarr_search_mode(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "sonarr_search_mode": "season_context"}
    app.post("/settings/instances", data=form, headers=csrf_headers(app))
    settings_resp = app.get("/settings")
    assert settings_resp.status_code == 200

    edit_resp = app.get("/settings/instances/1/edit")
    assert edit_resp.status_code == 200
    assert b'name="sonarr_search_mode"' in edit_resp.content
    assert b'value="season_context"' in edit_resp.content
    assert b"selected" in edit_resp.content


def test_create_radarr_forces_episode_search_mode(app: TestClient) -> None:
    _login(app)
    form = {
        **_VALID_FORM,
        "name": "My Radarr",
        "type": "radarr",
        "url": "http://radarr:7878",
        "sonarr_search_mode": "season_context",
    }
    app.post("/settings/instances", data=form, headers=csrf_headers(app))
    edit_resp = app.get("/settings/instances/1/edit")
    assert edit_resp.status_code == 200
    assert b'value="episode"' in edit_resp.content


def test_connection_check_endpoint_success(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "sonarr", "url": "http://sonarr:8989", "api_key": "abc"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert b"Connection successful" in resp.content


def test_connection_check_endpoint_invalid_type(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "plex", "url": "http://sonarr:8989", "api_key": "abc"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"Invalid type" in resp.content


def test_create_instance_rejects_invalid_cutoff_controls(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "cutoff_batch_size": "0", "cutoff_cooldown_days": "-1"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Cutoff batch size" in resp.content


def test_password_change_success(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/account/password",
        data={
            "current_password": "ValidPass1!",
            "new_password": "BetterPass2!",
            "new_password_confirm": "BetterPass2!",
        },
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert b"Password updated successfully" in resp.content
    assert b'id="account-settings" open' in resp.content

    app.post("/logout", headers=csrf_headers(app))
    login_resp = app.post(
        "/login",
        data={"username": "admin", "password": "BetterPass2!"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 303


def test_password_change_requires_correct_current_password(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/account/password",
        data={
            "current_password": "WrongPass1!",
            "new_password": "AnotherGoodPass2!",
            "new_password_confirm": "AnotherGoodPass2!",
        },
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"Current password is incorrect" in resp.content
    assert b'id="account-settings" open' in resp.content


def test_password_change_requires_matching_confirmation(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/account/password",
        data={
            "current_password": "ValidPass1!",
            "new_password": "AnotherGoodPass2!",
            "new_password_confirm": "MismatchPass2!",
        },
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"New passwords do not match" in resp.content
    assert b'id="account-settings" open' in resp.content

"""Tests for the settings page routes (instance management)."""

from __future__ import annotations

from typing import Any

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
    async def _always_ok(self: ArrClient) -> dict[str, Any] | None:
        name = type(self).__name__.replace("Client", "")
        return {"appName": name, "version": "4.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _always_ok)


def _login(client: TestClient) -> None:
    """Complete setup + login so subsequent requests are authenticated."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


# ---------------------------------------------------------------------------
# Authentication guard - all settings routes require a session
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
    assert b"https://av1155.github.io/houndarr/docs/reference/instance-settings" in resp.content


def test_settings_page_hx_request_returns_content_fragment(app: TestClient) -> None:
    """HX-Request for /settings should return shell content fragment only."""
    _login(app)
    resp = app.get("/settings", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b'data-page-key="settings"' in resp.content
    assert b'id="instance-tbody"' in resp.content
    assert b"<html" not in resp.content


def test_settings_help_hx_request_returns_content_fragment(app: TestClient) -> None:
    """HX-Request for /settings/help should return shell content fragment only."""
    _login(app)
    resp = app.get("/settings/help", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b'data-page-key="settings-help"' in resp.content
    assert b"Instance Settings Help" in resp.content
    assert b"<html" not in resp.content


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


def test_htmx_validation_error_returns_empty_html_with_reswap_none(app: TestClient) -> None:
    """FastAPI's default 422 body is JSON; with the base-template config
    opting 422 into HTMX swaps, that JSON would render as raw text in a
    UI slot.  The app-level ``RequestValidationError`` handler returns
    an empty HTML body with ``HX-Reswap: none`` for HTMX requests so
    the swap is suppressed and no JSON leaks into the DOM.
    """
    _login(app)
    form = {**_VALID_FORM}
    form.pop("name")  # trigger FastAPI's automatic 422 on missing Form()
    headers = {**csrf_headers(app), "HX-Request": "true"}
    resp = app.post("/settings/instances", data=form, headers=headers)
    assert resp.status_code == 422
    assert resp.headers.get("content-type", "").startswith("text/html"), resp.headers
    assert resp.headers.get("hx-reswap") == "none", resp.headers
    assert resp.content == b""


def test_non_htmx_validation_error_still_returns_json(app: TestClient) -> None:
    """Non-HTMX API consumers must keep getting FastAPI's JSON 422."""
    _login(app)
    form = {**_VALID_FORM}
    form.pop("name")
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert resp.headers.get("content-type", "").startswith("application/json"), resp.headers
    payload = resp.json()
    assert "detail" in payload


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
    # API key field must not contain the real key - only the sentinel
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
    # Use the sentinel value - server must keep the original key
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


def test_update_rejects_type_mismatch(app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance update should be blocked if the remote app type mismatches."""
    _login(app)
    # Create the instance first with the default mock (appName matches).
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    # Now switch the mock to return a different appName.
    async def _radarr_response(self: ArrClient) -> dict[str, Any] | None:
        return {"appName": "Radarr", "version": "6.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _radarr_response)
    resp = app.post("/settings/instances/1", data=_VALID_FORM, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Type mismatch" in resp.content


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
    """Deleting a non-existent ID is idempotent - still 200."""
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
    assert b'name="post_release_grace_hrs" type="number" min="0"' in resp.content
    assert b'value="6"' in resp.content
    assert b'name="sonarr_search_mode"' in resp.content
    assert b"Season-context search (advanced)" in resp.content
    assert b'href="/settings/help"' not in resp.content
    assert b'target="_blank"' not in resp.content


def test_add_form_exposes_reset_button_and_default_attrs(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/instances/add-form")
    assert resp.status_code == 200
    # Reset button is present in the footer.
    assert b'id="instance-reset-btn"' in resp.content
    assert b'data-reset-instance-form="true"' in resp.content
    assert b"Reset to Defaults" in resp.content
    # Policy inputs expose their defaults via data-default-* attributes.
    assert b'data-default-value="2"' in resp.content  # batch_size default
    assert b'data-default-value="30"' in resp.content  # sleep_interval_mins default
    assert b'data-default-value="14"' in resp.content  # cooldown_days default
    assert b'data-default-checked="0"' in resp.content  # cutoff/upgrade defaults
    # Connection fields must NOT carry default attributes.
    assert b'name="name"' in resp.content
    assert b'name="url"' in resp.content
    name_segment_idx = resp.content.find(b'name="name"')
    name_tag_end = resp.content.find(b"/>", name_segment_idx)
    assert b"data-default-value" not in resp.content[name_segment_idx:name_tag_end]


def test_edit_form_default_and_live_value_are_independent(app: TestClient) -> None:
    _login(app)
    created = app.post(
        "/settings/instances",
        data={**_VALID_FORM, "batch_size": "50", "cooldown_days": "99"},
        headers=csrf_headers(app),
    )
    assert created.status_code == 200

    resp = app.get("/settings/instances/1/edit")
    assert resp.status_code == 200
    # Live value reflects the saved configuration.
    assert b'value="50"' in resp.content
    assert b'value="99"' in resp.content
    # Default attributes continue to expose the Houndarr defaults.
    assert b'data-default-value="2"' in resp.content
    assert b'data-default-value="14"' in resp.content
    # Reset button is available in edit mode too.
    assert b'id="instance-reset-btn"' in resp.content


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
    assert b"Connected to Sonarr v4.0.0" in resp.content


def test_connection_check_type_mismatch(app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting Radarr for a Sonarr URL should report the mismatch."""

    async def _sonarr_response(self: ArrClient) -> dict[str, Any] | None:
        return {"appName": "Sonarr", "version": "4.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _sonarr_response)
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "radarr", "url": "http://sonarr:8989", "api_key": "abc"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"Type mismatch" in resp.content
    assert b"Sonarr" in resp.content


def test_connection_check_appname_null(app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing appName in system/status response should still succeed."""

    async def _no_appname(self: ArrClient) -> dict[str, Any] | None:
        return {"version": "4.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _no_appname)
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "sonarr", "url": "http://sonarr:8989", "api_key": "abc"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert b"Connection successful" in resp.content


def test_connection_check_appname_unknown(app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrecognized appName (e.g. a Readarr fork) should still succeed."""

    async def _fork_response(self: ArrClient) -> dict[str, Any] | None:
        return {"appName": "Bookshelf", "version": "1.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _fork_response)
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "readarr", "url": "http://readarr:8787", "api_key": "abc"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert b"Connected to Bookshelf" in resp.content


def test_create_rejects_type_mismatch(app: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance creation should be blocked if the remote app type mismatches."""

    async def _radarr_response(self: ArrClient) -> dict[str, Any] | None:
        return {"appName": "Radarr", "version": "6.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _radarr_response)
    _login(app)
    form = {**_VALID_FORM, "type": "sonarr"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Type mismatch" in resp.content


def test_connection_check_endpoint_invalid_type(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/instances/test-connection",
        data={"type": "plex", "url": "http://sonarr:8989", "api_key": "abc"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"Invalid instance type" in resp.content


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


def test_password_change_htmx(app: TestClient) -> None:
    """Password change via HTMX returns success message."""
    _login(app)
    resp = app.post(
        "/settings/account/password",
        data={
            "current_password": "ValidPass1!",
            "new_password": "BetterPass2!",
            "new_password_confirm": "BetterPass2!",
        },
        headers={**csrf_headers(app), "HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert b"Password updated successfully" in resp.content


# ---------------------------------------------------------------------------
# allowed_time_window validation on create + update
# ---------------------------------------------------------------------------


def test_create_accepts_valid_time_window(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "allowed_time_window": "09:00-23:00"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 200


def test_create_rejects_malformed_time_window(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "allowed_time_window": "9:00-bogus"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Invalid time range" in resp.content


def test_create_rejects_out_of_range_hour(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "allowed_time_window": "25:00-26:00"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Out-of-range" in resp.content


def test_create_persists_time_window_through_round_trip(app: TestClient) -> None:
    """A valid window should survive round-trip to the DB and reappear in the edit form."""
    _login(app)
    form = {**_VALID_FORM, "allowed_time_window": "22:00-06:00"}
    create_resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert create_resp.status_code == 200

    # The first (and only) instance in this test DB has id=1.
    edit_resp = app.get("/settings/instances/1/edit")
    assert edit_resp.status_code == 200
    # The form must pre-fill the value.
    assert b'value="22:00-06:00"' in edit_resp.content


def test_create_canonicalizes_whitespace_and_stores_normalized_form(
    app: TestClient,
) -> None:
    """Inner whitespace around the comma should be normalized out on save."""
    _login(app)
    form = {
        **_VALID_FORM,
        "allowed_time_window": "  09:00-12:00 , 14:00-22:00  ",
    }
    create_resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert create_resp.status_code == 200

    edit_resp = app.get("/settings/instances/1/edit")
    assert b'value="09:00-12:00,14:00-22:00"' in edit_resp.content


def test_update_rejects_malformed_time_window(app: TestClient) -> None:
    """Updating with an invalid window is a 422 and leaves the old value intact."""
    _login(app)
    # First create a valid instance.
    create_resp = app.post(
        "/settings/instances",
        data={**_VALID_FORM, "allowed_time_window": "09:00-18:00"},
        headers=csrf_headers(app),
    )
    assert create_resp.status_code == 200

    # Now try to update with a bogus spec.
    bad_form = {**_VALID_FORM, "allowed_time_window": "not-a-window"}
    update_resp = app.post("/settings/instances/1", data=bad_form, headers=csrf_headers(app))
    assert update_resp.status_code == 422
    assert b"Invalid time range" in update_resp.content

    # The stored value must still be the original.
    edit_resp = app.get("/settings/instances/1/edit")
    assert b'value="09:00-18:00"' in edit_resp.content


# ---------------------------------------------------------------------------
# search_order (#394)
# ---------------------------------------------------------------------------


def _option_selected(content: bytes, value: str) -> bool:
    """Return True when `<option value="{value}" ... selected ...>` appears in *content*.

    Matches `selected` strictly inside the opening tag of the target option so
    that "option exists" and "something else is selected" cannot both be true
    simultaneously and pass the assertion.
    """
    import re

    pattern = rb'<option\s+value="' + re.escape(value).encode("ascii") + rb'"[^>]*\sselected\b'
    return re.search(pattern, content) is not None


def test_add_form_preselects_random_by_default(app: TestClient) -> None:
    """A fresh Add Instance form pre-selects Random.

    Regression guard for the case where ``_blank_instance()`` or the Instance
    dataclass default silently falls back to chronological.  A real browser
    submits the selected option's ``value``; if this renders chronological,
    new instances get chronological persisted without user interaction.
    """
    _login(app)
    resp = app.get("/settings/instances/add-form")
    assert resp.status_code == 200
    assert b'name="search_order"' in resp.content
    assert _option_selected(resp.content, "random"), resp.content
    assert not _option_selected(resp.content, "chronological")


def test_create_instance_accepts_random_search_order(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "search_order": "random"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 200
    edit = app.get("/settings/instances/1/edit")
    assert b'name="search_order"' in edit.content
    assert _option_selected(edit.content, "random"), edit.content


def test_create_instance_defaults_to_random(app: TestClient) -> None:
    _login(app)
    resp = app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))
    assert resp.status_code == 200
    edit = app.get("/settings/instances/1/edit")
    assert _option_selected(edit.content, "random"), edit.content


def test_create_instance_rejects_invalid_search_order(app: TestClient) -> None:
    _login(app)
    form = {**_VALID_FORM, "search_order": "backwards"}
    resp = app.post("/settings/instances", data=form, headers=csrf_headers(app))
    assert resp.status_code == 422
    assert b"Invalid search order" in resp.content


def test_update_instance_toggles_search_order(app: TestClient) -> None:
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    update_form = {**_VALID_FORM, "search_order": "random"}
    resp = app.post("/settings/instances/1", data=update_form, headers=csrf_headers(app))
    assert resp.status_code == 200

    edit = app.get("/settings/instances/1/edit")
    assert _option_selected(edit.content, "random")

    revert = app.post(
        "/settings/instances/1",
        data={**_VALID_FORM, "search_order": "chronological"},
        headers=csrf_headers(app),
    )
    assert revert.status_code == 200
    edit_after = app.get("/settings/instances/1/edit")
    assert _option_selected(edit_after.content, "chronological")

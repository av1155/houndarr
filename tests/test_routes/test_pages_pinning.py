"""Pin the HTML page routes: setup / login / logout / dashboard / logs / settings help.

Locks the auth redirect rules, HX-vs-full-page branching,
validation-error shapes, and status codes that the Jinja templates
and HTMX client rely on.  These are end-to-end tests driven by
TestClient against a fresh TestClient fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.pinning


# Setup page


class TestSetupPage:
    def test_get_setup_before_setup_complete_renders(self, app: TestClient) -> None:
        resp = app.get("/setup")
        assert resp.status_code == 200
        assert "setup" in resp.text.lower() or "password" in resp.text.lower()

    def test_post_setup_rejects_short_password(self, app: TestClient) -> None:
        resp = app.post(
            "/setup",
            data={"username": "admin", "password": "short", "password_confirm": "short"},
        )
        assert resp.status_code == 422
        assert "at least 8" in resp.text or "8 characters" in resp.text

    def test_post_setup_rejects_mismatched_passwords(self, app: TestClient) -> None:
        resp = app.post(
            "/setup",
            data={"username": "admin", "password": "ValidPass1!", "password_confirm": "NoMatch1!"},
        )
        assert resp.status_code == 422
        assert "do not match" in resp.text.lower()

    def test_post_setup_rejects_invalid_username(self, app: TestClient) -> None:
        resp = app.post(
            "/setup",
            data={"username": "ab", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
        )
        assert resp.status_code == 422

    def test_post_setup_happy_path_redirects_303(self, app: TestClient) -> None:
        resp = app.post(
            "/setup",
            data={
                "username": "admin",
                "password": "ValidPass1!",
                "password_confirm": "ValidPass1!",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"

    def test_get_setup_after_complete_redirects_to_login(self, app: TestClient) -> None:
        app.post(
            "/setup",
            data={
                "username": "admin",
                "password": "ValidPass1!",
                "password_confirm": "ValidPass1!",
            },
        )
        resp = app.get("/setup", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


# Login / logout


class TestLogin:
    def test_get_login_before_setup_redirects(self, app: TestClient) -> None:
        resp = app.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/setup"

    def test_post_login_invalid_returns_401(self, app: TestClient) -> None:
        app.post(
            "/setup",
            data={
                "username": "admin",
                "password": "ValidPass1!",
                "password_confirm": "ValidPass1!",
            },
        )
        resp = app.post("/login", data={"username": "admin", "password": "WrongPass1!"})
        assert resp.status_code == 401
        assert "invalid" in resp.text.lower()

    def test_post_login_happy_path_redirects_to_root(self, app: TestClient) -> None:
        app.post(
            "/setup",
            data={
                "username": "admin",
                "password": "ValidPass1!",
                "password_confirm": "ValidPass1!",
            },
        )
        resp = app.post(
            "/login",
            data={"username": "admin", "password": "ValidPass1!"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

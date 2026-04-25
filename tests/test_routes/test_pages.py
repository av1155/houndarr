"""Tests for the HTML page routes in ``src/houndarr/routes/pages.py``.

Covers the setup and login entry points, the authenticated shell pages
(dashboard, logs, settings help), and the HTMX partial branch on the
dashboard.  Until now these routes were only exercised by the
integration suites; this file gives them unit-level coverage so
regressions surface against ``pytest -m "not integration"``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _setup_and_login(client: TestClient) -> None:
    """Run first-run setup and log in so protected pages render."""
    client.post(
        "/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


# ---------------------------------------------------------------------------
# Setup and login entry points
# ---------------------------------------------------------------------------


def test_setup_page_renders_when_not_configured(app: TestClient) -> None:
    """A fresh install lands on /setup for first-run password configuration."""
    resp = app.get("/setup")
    assert resp.status_code == 200
    assert b"password" in resp.content.lower()


def test_setup_page_redirects_to_login_after_configured(app: TestClient) -> None:
    """Once setup has run, /setup bounces to /login instead of re-rendering."""
    _setup_and_login(app)
    resp = app.get("/setup", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_login_page_renders_when_configured(app: TestClient) -> None:
    """After setup, /login renders the credentials form."""
    app.post(
        "/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    resp = app.get("/login")
    assert resp.status_code == 200
    assert b"password" in resp.content.lower()


def test_login_page_redirects_to_setup_before_configured(app: TestClient) -> None:
    """/login before setup bounces to /setup so the operator completes it first."""
    resp = app.get("/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "/setup" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Authenticated shell pages
# ---------------------------------------------------------------------------


def test_dashboard_renders_when_authenticated(app: TestClient) -> None:
    _setup_and_login(app)
    resp = app.get("/")
    assert resp.status_code == 200
    assert b"<!doctype html>" in resp.content.lower()


def test_dashboard_returns_partial_on_htmx_request(app: TestClient) -> None:
    """HTMX navigations get the inner partial, not the full page shell."""
    _setup_and_login(app)
    resp = app.get("/", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    # The partial has no doctype; the full dashboard template does.
    assert b"<!doctype html>" not in resp.content.lower()


def test_logs_page_renders_when_authenticated(app: TestClient) -> None:
    _setup_and_login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200


def test_logs_page_survives_malformed_query_string(app: TestClient) -> None:
    """Bad filter values fall back to the unfiltered view rather than 422."""
    _setup_and_login(app)
    resp = app.get("/logs?instance_id=notanint&search_kind=bogus")
    assert resp.status_code == 200


def test_settings_help_page_renders_when_authenticated(app: TestClient) -> None:
    _setup_and_login(app)
    resp = app.get("/settings/help")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_redirects_to_login(app: TestClient) -> None:
    _setup_and_login(app)
    resp = app.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]

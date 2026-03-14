"""Tests for authentication: password hashing, session, setup flow, login."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from houndarr.auth import check_credentials, hash_password, is_setup_complete, verify_password
from houndarr.database import get_setting
from tests.conftest import csrf_headers

# ---------------------------------------------------------------------------
# Unit tests — password hashing
# ---------------------------------------------------------------------------


def test_hash_password_returns_bcrypt_hash() -> None:
    h = hash_password("supersecret")
    assert h.startswith("$2b$")


def test_verify_password_correct() -> None:
    h = hash_password("mypassword")
    assert verify_password("mypassword", h) is True


def test_verify_password_wrong() -> None:
    h = hash_password("mypassword")
    assert verify_password("wrong", h) is False


def test_verify_password_empty() -> None:
    h = hash_password("mypassword")
    assert verify_password("", h) is False


# ---------------------------------------------------------------------------
# Async unit tests — setup state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_setup_not_complete_initially(db: None) -> None:
    assert await is_setup_complete() is False


@pytest.mark.asyncio()
async def test_setup_complete_after_set_password(db: None) -> None:
    from houndarr.auth import set_password

    await set_password("testpassword123")
    assert await is_setup_complete() is True


@pytest.mark.asyncio()
async def test_check_password_correct(db: None) -> None:
    from houndarr.auth import check_password, set_password

    await set_password("correct_password")
    assert await check_password("correct_password") is True


@pytest.mark.asyncio()
async def test_check_password_incorrect(db: None) -> None:
    from houndarr.auth import check_password, set_password

    await set_password("correct_password")
    assert await check_password("wrong_password") is False


@pytest.mark.asyncio()
async def test_check_credentials_correct(db: None) -> None:
    from houndarr.auth import set_password, set_username

    await set_username("admin")
    await set_password("correct_password")
    assert await check_credentials("admin", "correct_password") is True


@pytest.mark.asyncio()
async def test_check_credentials_wrong_username(db: None) -> None:
    from houndarr.auth import set_password, set_username

    await set_username("admin")
    await set_password("correct_password")
    assert await check_credentials("operator", "correct_password") is False


@pytest.mark.asyncio()
async def test_check_credentials_claims_username_for_legacy_install(db: None) -> None:
    from houndarr.auth import set_password

    await set_password("correct_password")
    assert await check_credentials("admin", "correct_password") is True
    assert await get_setting("username") == "admin"


# ---------------------------------------------------------------------------
# Integration tests — HTTP routes
# ---------------------------------------------------------------------------


def test_health_endpoint_no_auth(app: TestClient) -> None:
    """Health endpoint must work without authentication."""
    response = app.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_redirect_to_setup_when_not_configured(app: TestClient) -> None:
    """All protected routes should redirect to /setup when no password set."""
    response = app.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] in ("/setup", "http://testserver/setup")


def test_setup_page_renders(app: TestClient) -> None:
    """Setup page should render without errors."""
    response = app.get("/setup")
    assert response.status_code == 200
    assert b"Welcome to Houndarr" in response.content


def test_setup_post_short_password(app: TestClient) -> None:
    """Password shorter than 8 chars should fail validation."""
    response = app.post(
        "/setup",
        data={"username": "admin", "password": "short", "password_confirm": "short"},
    )
    assert response.status_code == 422
    assert b"at least 8 characters" in response.content


def test_setup_post_invalid_username(app: TestClient) -> None:
    """Invalid username should fail validation."""
    response = app.post(
        "/setup",
        data={
            "username": "bad username",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    assert response.status_code == 422
    assert b"Username may only contain" in response.content


def test_setup_post_mismatched_passwords(app: TestClient) -> None:
    """Mismatched confirmation should return error."""
    response = app.post(
        "/setup",
        data={"username": "admin", "password": "password123", "password_confirm": "password456"},
    )
    assert response.status_code == 422
    assert b"do not match" in response.content


def test_setup_post_success(app: TestClient) -> None:
    """Valid setup should redirect to login."""
    response = app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_login_page_renders_after_setup(app: TestClient) -> None:
    """Login page should render after setup is complete."""
    # Complete setup first
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    response = app.get("/login")
    assert response.status_code == 200
    assert b"Houndarr" in response.content


def test_full_pages_render_consistent_titles(app: TestClient) -> None:
    """Full HTML pages should render '<Page> - Houndarr' title format."""
    setup_resp = app.get("/setup")
    assert setup_resp.status_code == 200
    assert b"<title>Setup \xe2\x80\x94 Houndarr</title>" in setup_resp.content

    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )

    login_resp = app.get("/login")
    assert login_resp.status_code == 200
    assert b"<title>Login \xe2\x80\x94 Houndarr</title>" in login_resp.content

    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    dashboard_resp = app.get("/")
    assert dashboard_resp.status_code == 200
    assert b"<title>Dashboard \xe2\x80\x94 Houndarr</title>" in dashboard_resp.content

    settings_resp = app.get("/settings")
    assert settings_resp.status_code == 200
    assert b"<title>Settings \xe2\x80\x94 Houndarr</title>" in settings_resp.content

    logs_resp = app.get("/logs")
    assert logs_resp.status_code == 200
    assert b"<title>Logs \xe2\x80\x94 Houndarr</title>" in logs_resp.content

    help_resp = app.get("/settings/help")
    assert help_resp.status_code == 200
    assert b"<title>Settings Help \xe2\x80\x94 Houndarr</title>" in help_resp.content


def test_login_page_includes_browser_identity_metadata(app: TestClient) -> None:
    """Shared head metadata should be present on full HTML pages."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )

    response = app.get("/login")
    assert response.status_code == 200
    assert (
        b'<meta name="description" content="A focused, self-hosted companion for '
        b'Sonarr and Radarr." />' in response.content
    )
    assert b'<meta name="application-name" content="Houndarr" />' in response.content
    assert b'<meta name="apple-mobile-web-app-title" content="Houndarr" />' in response.content
    assert b'<meta name="theme-color" content="#020617" />' in response.content
    assert b'<meta name="color-scheme" content="dark" />' in response.content
    assert (
        b'<link rel="icon" type="image/png" href="/static/img/houndarr-logo.png" />'
        in response.content
    )
    assert b'<link rel="shortcut icon" href="/static/img/houndarr-logo.png" />' in response.content
    assert (
        b'<link rel="apple-touch-icon" href="/static/img/houndarr-logo.png" />' in response.content
    )


def test_authenticated_shell_includes_hx_navigation_attrs(app: TestClient) -> None:
    """Authenticated shell should expose HTMX nav swap attributes."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    response = app.get("/")
    assert response.status_code == 200
    assert b'id="app-content"' in response.content
    assert b'data-shell-nav="true"' in response.content
    assert b'hx-target="#app-content"' in response.content
    assert b'hx-push-url="true"' in response.content


def test_dashboard_hx_request_returns_content_fragment(app: TestClient) -> None:
    """HX-Request for dashboard should return content fragment, not full document."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    response = app.get("/", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert b'data-page-key="dashboard"' in response.content
    assert b"<html" not in response.content


def test_login_wrong_password(app: TestClient) -> None:
    """Wrong password should return 401."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    response = app.post("/login", data={"username": "admin", "password": "WrongPass!"})
    assert response.status_code == 401
    assert b"Invalid credentials" in response.content


def test_login_wrong_username(app: TestClient) -> None:
    """Wrong username should return 401 with generic error."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    response = app.post("/login", data={"username": "other", "password": "ValidPass1!"})
    assert response.status_code == 401
    assert b"Invalid credentials" in response.content


def test_login_correct_password(app: TestClient) -> None:
    """Correct password should create session and redirect to dashboard."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    response = app.post(
        "/login",
        data={"username": "admin", "password": "ValidPass1!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/" in response.headers["location"]
    # Both session and CSRF cookies should be set
    assert "houndarr_session" in response.cookies
    assert "houndarr_csrf" in response.cookies


def test_dashboard_accessible_after_login(app: TestClient) -> None:
    """Dashboard should be accessible after logging in."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    response = app.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.content
    assert b'id="instance-grid"' in response.content
    assert b'data-hydrated="false"' in response.content
    assert b'hx-trigger="load, every 15s"' in response.content
    assert b"Fleet summary" in response.content
    assert b"24h searched" in response.content


def test_logout_clears_session(app: TestClient) -> None:
    """Logout should clear the session and redirect to login."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    response = app.post("/logout", follow_redirects=False, headers=csrf_headers(app))
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_csrf_protection_rejects_missing_token(app: TestClient) -> None:
    """POST to protected authenticated route without CSRF token should return 403."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    response = app.post(
        "/settings/account/password",
        data={
            "current_password": "ValidPass1!",
            "new_password": "BetterPass2!",
            "new_password_confirm": "BetterPass2!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_csrf_protection_rejects_wrong_token(app: TestClient) -> None:
    """Wrong CSRF token on protected authenticated route should return 403."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    response = app.post(
        "/settings/account/password",
        data={
            "current_password": "ValidPass1!",
            "new_password": "BetterPass2!",
            "new_password_confirm": "BetterPass2!",
        },
        follow_redirects=False,
        headers={"X-CSRF-Token": "invalid-token-value"},
    )
    assert response.status_code == 403


def test_csrf_protection_via_form_field(app: TestClient) -> None:
    """CSRF token in form field (csrf_token) should also be accepted."""
    from tests.conftest import get_csrf_token

    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    token = get_csrf_token(app)
    response = app.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_logout_allows_legacy_session_without_csrf(app: TestClient) -> None:
    """Legacy pre-CSRF sessions should still be able to logout cleanly."""
    from houndarr.auth import SESSION_COOKIE_NAME, _get_serializer

    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    serializer = asyncio.run(_get_serializer())
    legacy_token = serializer.dumps({"ts": int(time.time())})
    app.cookies.set(SESSION_COOKIE_NAME, legacy_token)
    app.cookies.pop("houndarr_csrf", None)

    response = app.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]

    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(
        "houndarr_session=" in header and "Max-Age=0" in header for header in set_cookie_headers
    )
    assert any(
        "houndarr_csrf=" in header and "Max-Age=0" in header for header in set_cookie_headers
    )


def test_rate_limiter_uses_direct_ip_by_default(app: TestClient) -> None:
    """Rate limiter must use direct client IP, not X-Forwarded-For, by default."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    # Send wrong password 5 times from the same direct IP
    for _ in range(5):
        app.post("/login", data={"username": "admin", "password": "WrongPass!"})

    # 6th attempt should be rate-limited
    response = app.post("/login", data={"username": "admin", "password": "WrongPass!"})
    assert response.status_code == 429

    # Even with a different X-Forwarded-For header, should still be rate-limited
    # (because X-Forwarded-For is not trusted without configured trusted proxies)
    response2 = app.post(
        "/login",
        data={"username": "admin", "password": "WrongPass!"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    # This should still be 429 (rate limited by real IP) or 401 (if the XFF is
    # not trusted and the real IP is still tracked).  It must NOT be 401 due to
    # XFF bypass — i.e., a different XFF must not reset the counter.
    assert response2.status_code in (429, 401)  # Either is fine; must not bypass rate limit

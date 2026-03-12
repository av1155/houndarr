"""Tests for authentication: password hashing, session, setup flow, login."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from houndarr.auth import hash_password, is_setup_complete, verify_password

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
    response = app.post("/setup", data={"password": "short", "password_confirm": "short"})
    assert response.status_code == 422
    assert b"at least 8 characters" in response.content


def test_setup_post_mismatched_passwords(app: TestClient) -> None:
    """Mismatched confirmation should return error."""
    response = app.post(
        "/setup",
        data={"password": "password123", "password_confirm": "password456"},
    )
    assert response.status_code == 422
    assert b"do not match" in response.content


def test_setup_post_success(app: TestClient) -> None:
    """Valid setup should redirect to login."""
    response = app.post(
        "/setup",
        data={"password": "ValidPass1!", "password_confirm": "ValidPass1!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_login_page_renders_after_setup(app: TestClient) -> None:
    """Login page should render after setup is complete."""
    # Complete setup first
    app.post(
        "/setup",
        data={"password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    response = app.get("/login")
    assert response.status_code == 200
    assert b"Houndarr" in response.content


def test_login_wrong_password(app: TestClient) -> None:
    """Wrong password should return 401."""
    app.post("/setup", data={"password": "ValidPass1!", "password_confirm": "ValidPass1!"})
    response = app.post("/login", data={"password": "WrongPass!"})
    assert response.status_code == 401
    assert b"Incorrect password" in response.content


def test_login_correct_password(app: TestClient) -> None:
    """Correct password should create session and redirect to dashboard."""
    app.post("/setup", data={"password": "ValidPass1!", "password_confirm": "ValidPass1!"})
    response = app.post(
        "/login",
        data={"password": "ValidPass1!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/" in response.headers["location"]
    # Session cookie should be set
    assert "houndarr_session" in response.cookies


def test_dashboard_accessible_after_login(app: TestClient) -> None:
    """Dashboard should be accessible after logging in."""
    app.post("/setup", data={"password": "ValidPass1!", "password_confirm": "ValidPass1!"})
    app.post("/login", data={"password": "ValidPass1!"})
    response = app.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.content


def test_logout_clears_session(app: TestClient) -> None:
    """Logout should clear the session and redirect to login."""
    app.post("/setup", data={"password": "ValidPass1!", "password_confirm": "ValidPass1!"})
    app.post("/login", data={"password": "ValidPass1!"})
    response = app.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]

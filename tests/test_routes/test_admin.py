"""Integration tests for /settings/admin/* routes in both auth modes."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import houndarr.auth as _auth_mod
import houndarr.config as _cfg
from houndarr.auth import CSRF_COOKIE_NAME
from houndarr.clients._wire_models.common import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.config import AppSettings
from houndarr.database import get_db
from tests.conftest import csrf_headers

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_TRUSTED_IP = "172.18.0.5"
_AUTH_HEADER = "Remote-User"
_AUTH_USER = "alice"


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance creation below calls ArrClient.ping during the route flow."""

    async def _always_ok(self: ArrClient) -> SystemStatus | None:
        name = type(self).__name__.replace("Client", "")
        return SystemStatus(app_name=name, version="4.0.0")

    monkeypatch.setattr(ArrClient, "ping", _always_ok)


def _login(client: TestClient) -> None:
    """Complete setup + login so subsequent builtin requests are authenticated."""
    client.post(
        "/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


def _proxy_csrf(client: TestClient) -> str:
    """Prime the CSRF cookie in proxy mode and return its value."""
    client.get("/", headers={_AUTH_HEADER: _AUTH_USER})
    return client.cookies.get(CSRF_COOKIE_NAME, "")


# ---------------------------------------------------------------------------
# Proxy-mode fixtures mirror tests/test_proxy_auth.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def proxy_settings(tmp_data_dir: str) -> AppSettings:
    settings = AppSettings(
        data_dir=tmp_data_dir,
        auth_mode="proxy",
        auth_proxy_header=_AUTH_HEADER,
        trusted_proxies=_TRUSTED_IP,
    )
    _cfg._runtime_settings = settings  # noqa: SLF001
    _auth_mod._serializer = None  # noqa: SLF001
    _auth_mod._setup_complete = None  # noqa: SLF001
    _auth_mod._login_attempts.clear()  # noqa: SLF001
    return settings


@pytest.fixture()
def proxy_app(proxy_settings: AppSettings) -> Generator[TestClient, None, None]:
    from houndarr.app import create_app

    application = create_app()
    original_app = application

    async def _patched_app(scope, receive, send):  # type: ignore[no-untyped-def]  # noqa: ANN001
        if scope["type"] == "http":
            scope["client"] = (_TRUSTED_IP, 0)
        await original_app(scope, receive, send)

    with TestClient(_patched_app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Unauth / CSRF guards (builtin)
# ---------------------------------------------------------------------------


_ADMIN_POSTS = [
    "/settings/admin/reset-instances",
    "/settings/admin/clear-logs",
    "/settings/admin/factory-reset",
]


@pytest.mark.parametrize("path", _ADMIN_POSTS)
def test_admin_post_redirects_unauthenticated(app: TestClient, path: str) -> None:
    resp = app.post(path, follow_redirects=False)
    assert resp.status_code in (302, 303)


@pytest.mark.parametrize("path", _ADMIN_POSTS)
def test_admin_post_requires_csrf(app: TestClient, path: str) -> None:
    _login(app)
    # No X-CSRF-Token header → middleware rejects.
    resp = app.post(path, follow_redirects=False)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Reset all instance settings
# ---------------------------------------------------------------------------


_VALID_INSTANCE = {
    "name": "My Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "api_key": "test-api-key-abc123",
    "sonarr_search_mode": "episode",
    "connection_verified": "true",
}


def test_reset_instances_happy_path(app: TestClient) -> None:
    _login(app)
    # Create an instance with a non-default batch_size so we can observe the reset.
    app.post(
        "/settings/instances",
        data={**_VALID_INSTANCE, "batch_size": 99},
        headers=csrf_headers(app),
    )
    resp = app.post(
        "/settings/admin/reset-instances",
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert b"Policy settings reset" in resp.content


def test_reset_instances_no_instances_returns_flash(app: TestClient) -> None:
    _login(app)
    resp = app.post(
        "/settings/admin/reset-instances",
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert b"No instances configured" in resp.content


# ---------------------------------------------------------------------------
# Clear all logs
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def seed_one_log_row() -> AsyncGenerator[None, None]:
    """Insert one search_log row before the test so clear-logs removes something."""
    async with get_db() as conn:
        await conn.execute("INSERT INTO search_log (action, message) VALUES ('info', 'seed')")
        await conn.commit()
    yield


def test_clear_logs_happy_path(
    app: TestClient,
    seed_one_log_row: None,
) -> None:
    _login(app)
    resp = app.post("/settings/admin/clear-logs", headers=csrf_headers(app))
    assert resp.status_code == 200
    # Message varies by rowcount; the success flash should mention "Cleared".
    assert b"Cleared" in resp.content or b"already empty" in resp.content


# ---------------------------------------------------------------------------
# Factory reset - builtin mode
# ---------------------------------------------------------------------------


@pytest.fixture()
def _stub_factory_reset(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub out the destructive factory_reset call so tests don't wipe tmp_data_dir."""
    stub = AsyncMock(return_value=None)
    monkeypatch.setattr("houndarr.routes.admin.factory_reset", stub)
    return stub


def test_factory_reset_rejects_wrong_phrase(
    app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    _login(app)
    resp = app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "reset", "current_password": "ValidPass1!"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"Type RESET" in resp.content
    _stub_factory_reset.assert_not_called()


def test_factory_reset_rejects_wrong_password(
    app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    _login(app)
    resp = app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "current_password": "not-the-password"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    assert b"password is incorrect" in resp.content
    _stub_factory_reset.assert_not_called()


def test_factory_reset_rejects_missing_password(
    app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    _login(app)
    resp = app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 422
    _stub_factory_reset.assert_not_called()


def test_factory_reset_happy_path(
    app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    _login(app)
    resp = app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "current_password": "ValidPass1!"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/setup"
    _stub_factory_reset.assert_awaited_once()


def test_factory_reset_rate_limit_returns_429_after_five_bad_attempts(
    app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    """Six failed attempts trip the shared /login bucket so a session-
    compromised attacker cannot brute-force the admin password through
    the destructive /settings/admin/factory-reset endpoint.
    """
    _login(app)
    bad = {"confirm_phrase": "RESET", "current_password": "not-the-password"}
    for _ in range(5):
        resp = app.post(
            "/settings/admin/factory-reset",
            data=bad,
            headers=csrf_headers(app),
        )
        assert resp.status_code == 422

    resp = app.post(
        "/settings/admin/factory-reset",
        data=bad,
        headers=csrf_headers(app),
    )
    assert resp.status_code == 429
    assert b"Too many attempts" in resp.content
    _stub_factory_reset.assert_not_called()


# ---------------------------------------------------------------------------
# Proxy mode
# ---------------------------------------------------------------------------


def test_reset_instances_proxy_happy_path(proxy_app: TestClient) -> None:
    csrf = _proxy_csrf(proxy_app)
    resp = proxy_app.post(
        "/settings/admin/reset-instances",
        headers={_AUTH_HEADER: _AUTH_USER, "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200


def test_reset_instances_proxy_requires_csrf(proxy_app: TestClient) -> None:
    resp = proxy_app.post(
        "/settings/admin/reset-instances",
        headers={_AUTH_HEADER: _AUTH_USER},
    )
    assert resp.status_code == 403


def test_clear_logs_proxy_happy_path(proxy_app: TestClient) -> None:
    csrf = _proxy_csrf(proxy_app)
    resp = proxy_app.post(
        "/settings/admin/clear-logs",
        headers={_AUTH_HEADER: _AUTH_USER, "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200


def test_factory_reset_proxy_rejects_wrong_username(
    proxy_app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    csrf = _proxy_csrf(proxy_app)
    resp = proxy_app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "confirm_username": "not-alice"},
        headers={_AUTH_HEADER: _AUTH_USER, "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422
    assert b"does not match" in resp.content
    _stub_factory_reset.assert_not_called()


def test_factory_reset_proxy_happy_path(
    proxy_app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    csrf = _proxy_csrf(proxy_app)
    resp = proxy_app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "confirm_username": _AUTH_USER},
        headers={_AUTH_HEADER: _AUTH_USER, "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/"
    _stub_factory_reset.assert_awaited_once()


def test_factory_reset_proxy_username_case_insensitive(
    proxy_app: TestClient,
    _stub_factory_reset: AsyncMock,
) -> None:
    csrf = _proxy_csrf(proxy_app)
    resp = proxy_app.post(
        "/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "confirm_username": _AUTH_USER.upper()},
        headers={_AUTH_HEADER: _AUTH_USER, "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/"


# ---------------------------------------------------------------------------
# Settings page rendering in proxy mode: Security sub-section hides the
# password form and echoes the proxy identity instead.
# ---------------------------------------------------------------------------


def test_settings_security_in_proxy_renders_signed_in_card(proxy_app: TestClient) -> None:
    resp = proxy_app.get("/settings", headers={_AUTH_HEADER: _AUTH_USER})
    assert resp.status_code == 200
    # Signed-in-as echoes the proxy user, not the stored admin username.
    assert _AUTH_USER.encode() in resp.content
    # Password form is hidden in proxy mode; no Update Password submit.
    assert b"Update Password" not in resp.content
    # Factory reset prompts for the typed username instead of password.
    assert b"Type your current username" in resp.content
    assert b"current_password" not in resp.content

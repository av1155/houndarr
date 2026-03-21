"""Integration tests for proxy authentication mode.

Tests verify the security invariants defined in the proxy auth plan:
  S1: Proxy mode requires AUTH_PROXY_HEADER and TRUSTED_PROXIES at startup
  S2: Untrusted IPs get 403 in proxy mode
  S3: Trusted proxy without auth header gets 401
  S4: Auth header only read after IP trust verification
  S5: CSRF protection maintained in proxy mode
  S6: Setup/login flows disabled in proxy mode
  S7: App refuses to start with invalid proxy config (tested in test_config.py)
  S8: Reserved header names rejected at startup (tested in test_config.py)
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import houndarr.auth as _auth_mod
import houndarr.config as _cfg
from houndarr.auth import (
    CSRF_COOKIE_NAME,
    _is_proxy_auth_mode,
    _validate_proxy_auth,
)
from houndarr.config import AppSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRUSTED_IP = "172.18.0.5"
_UNTRUSTED_IP = "1.2.3.4"
_AUTH_HEADER = "Remote-User"
_AUTH_USER = "alice"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def proxy_settings(tmp_data_dir: str) -> AppSettings:
    """Configure proxy auth mode with a trusted IP."""
    settings = AppSettings(
        data_dir=tmp_data_dir,
        auth_mode="proxy",
        auth_proxy_header=_AUTH_HEADER,
        trusted_proxies=_TRUSTED_IP,
    )
    _cfg._runtime_settings = settings  # noqa: SLF001
    _auth_mod._serializer = None  # noqa: SLF001
    _auth_mod._login_attempts.clear()  # noqa: SLF001
    return settings


@pytest.fixture()
def proxy_app(proxy_settings: AppSettings) -> Generator[TestClient, None, None]:
    """TestClient for an app running in proxy auth mode.

    Starlette's TestClient uses ``"testclient"`` as the client host, which
    is not a valid IP.  To simulate a trusted proxy, we monkey-patch the
    ASGI scope to inject a real trusted IP address.
    """
    from houndarr.app import create_app

    application = create_app()

    # Wrap the ASGI app to inject a trusted-proxy client IP
    original_app = application

    async def _patched_app(scope, receive, send):  # type: ignore[no-untyped-def]  # noqa: ANN001
        if scope["type"] == "http":
            scope["client"] = (_TRUSTED_IP, 0)
        await original_app(scope, receive, send)

    with TestClient(_patched_app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def untrusted_proxy_app(proxy_settings: AppSettings) -> Generator[TestClient, None, None]:
    """TestClient simulating an untrusted (direct) connection in proxy mode."""
    from houndarr.app import create_app

    application = create_app()

    original_app = application

    async def _patched_app(scope, receive, send):  # type: ignore[no-untyped-def]  # noqa: ANN001
        if scope["type"] == "http":
            scope["client"] = (_UNTRUSTED_IP, 0)
        await original_app(scope, receive, send)

    with TestClient(_patched_app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Unit tests — _is_proxy_auth_mode / _validate_proxy_auth
# ---------------------------------------------------------------------------


def test_is_proxy_auth_mode_builtin(test_settings: AppSettings) -> None:
    """Default builtin mode returns False."""
    assert _is_proxy_auth_mode() is False


def test_is_proxy_auth_mode_proxy(proxy_settings: AppSettings) -> None:
    """Proxy mode returns True."""
    assert _is_proxy_auth_mode() is True


def test_validate_proxy_auth_trusted_with_header(proxy_settings: AppSettings) -> None:
    """Request from trusted IP with auth header returns username."""
    request = MagicMock()
    request.client.host = _TRUSTED_IP
    request.headers = {_AUTH_HEADER: _AUTH_USER}
    assert _validate_proxy_auth(request) == _AUTH_USER


def test_validate_proxy_auth_untrusted_with_header(proxy_settings: AppSettings) -> None:
    """Request from untrusted IP returns None even with valid auth header."""
    request = MagicMock()
    request.client.host = _UNTRUSTED_IP
    request.headers = {_AUTH_HEADER: _AUTH_USER}
    assert _validate_proxy_auth(request) is None


def test_validate_proxy_auth_trusted_missing_header(proxy_settings: AppSettings) -> None:
    """Request from trusted IP without auth header returns None."""
    request = MagicMock()
    request.client.host = _TRUSTED_IP
    request.headers = {}
    assert _validate_proxy_auth(request) is None


def test_validate_proxy_auth_trusted_empty_header(proxy_settings: AppSettings) -> None:
    """Request from trusted IP with blank auth header returns None."""
    request = MagicMock()
    request.client.host = _TRUSTED_IP
    request.headers = {_AUTH_HEADER: "   "}
    assert _validate_proxy_auth(request) is None


def test_validate_proxy_auth_no_client(proxy_settings: AppSettings) -> None:
    """Request with no client address returns None."""
    request = MagicMock()
    request.client = None
    request.headers = {_AUTH_HEADER: _AUTH_USER}
    assert _validate_proxy_auth(request) is None


# ---------------------------------------------------------------------------
# S2: Untrusted IPs get 403
# ---------------------------------------------------------------------------


def test_untrusted_ip_gets_403_dashboard(
    untrusted_proxy_app: TestClient,
) -> None:
    """Direct request to dashboard from untrusted IP returns 403."""
    resp = untrusted_proxy_app.get("/", follow_redirects=False)
    assert resp.status_code == 403


def test_untrusted_ip_gets_403_settings(
    untrusted_proxy_app: TestClient,
) -> None:
    """Direct request to settings from untrusted IP returns 403."""
    resp = untrusted_proxy_app.get("/settings", follow_redirects=False)
    assert resp.status_code == 403


def test_untrusted_ip_gets_403_api(
    untrusted_proxy_app: TestClient,
) -> None:
    """Direct request to API from untrusted IP returns 403."""
    resp = untrusted_proxy_app.get("/api/status", follow_redirects=False)
    assert resp.status_code == 403


def test_untrusted_ip_gets_403_even_with_header(
    untrusted_proxy_app: TestClient,
) -> None:
    """Header spoofing from untrusted IP still returns 403 (S4)."""
    resp = untrusted_proxy_app.get(
        "/",
        headers={_AUTH_HEADER: _AUTH_USER},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# S3: Trusted proxy without auth header gets 401
# ---------------------------------------------------------------------------


def test_trusted_proxy_missing_header_gets_401(
    proxy_app: TestClient,
) -> None:
    """Request from trusted proxy without auth header returns 401."""
    resp = proxy_app.get("/", follow_redirects=False)
    assert resp.status_code == 401


def test_trusted_proxy_empty_header_gets_401(
    proxy_app: TestClient,
) -> None:
    """Request from trusted proxy with blank auth header returns 401."""
    resp = proxy_app.get(
        "/",
        headers={_AUTH_HEADER: ""},
        follow_redirects=False,
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated access — trusted proxy with valid header
# ---------------------------------------------------------------------------


def test_proxy_auth_dashboard_accessible(
    proxy_app: TestClient,
) -> None:
    """Authenticated proxy request can access the dashboard."""
    resp = proxy_app.get(
        "/",
        headers={_AUTH_HEADER: _AUTH_USER},
        follow_redirects=False,
    )
    assert resp.status_code == 200


def test_proxy_auth_settings_accessible(
    proxy_app: TestClient,
) -> None:
    """Authenticated proxy request can access settings."""
    resp = proxy_app.get(
        "/settings",
        headers={_AUTH_HEADER: _AUTH_USER},
        follow_redirects=False,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# S5: CSRF protection in proxy mode
# ---------------------------------------------------------------------------


def test_proxy_csrf_cookie_set_on_auth_response(
    proxy_app: TestClient,
) -> None:
    """Authenticated response includes the CSRF cookie."""
    resp = proxy_app.get(
        "/",
        headers={_AUTH_HEADER: _AUTH_USER},
    )
    assert resp.status_code == 200
    assert CSRF_COOKIE_NAME in proxy_app.cookies


def test_proxy_csrf_required_for_post(
    proxy_app: TestClient,
) -> None:
    """POST without CSRF token returns 403."""
    # First GET to get CSRF cookie
    proxy_app.get("/", headers={_AUTH_HEADER: _AUTH_USER})

    # POST without CSRF token
    resp = proxy_app.post(
        "/settings/instances",
        data={"name": "test"},
        headers={_AUTH_HEADER: _AUTH_USER},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_proxy_csrf_valid_token_allows_post(
    proxy_app: TestClient,
) -> None:
    """POST with valid CSRF token succeeds (doesn't return 403)."""
    # First GET to get CSRF cookie
    proxy_app.get("/", headers={_AUTH_HEADER: _AUTH_USER})
    csrf_token = proxy_app.cookies.get(CSRF_COOKIE_NAME, "")
    assert csrf_token

    # POST with valid CSRF token — will get validation error (422) from
    # the route handler, not a 403 from CSRF check
    resp = proxy_app.post(
        "/settings/instances",
        data={"name": "test", "csrf_token": csrf_token},
        headers={_AUTH_HEADER: _AUTH_USER},
        follow_redirects=False,
    )
    # Not 403 (CSRF passed); the route handler may return 422 for bad form data
    assert resp.status_code != 403


def test_proxy_csrf_wrong_token_rejected(
    proxy_app: TestClient,
) -> None:
    """POST with wrong CSRF token returns 403."""
    # First GET to get CSRF cookie
    proxy_app.get("/", headers={_AUTH_HEADER: _AUTH_USER})

    # POST with wrong token
    resp = proxy_app.post(
        "/settings/instances",
        data={"name": "test", "csrf_token": "wrong-token"},
        headers={_AUTH_HEADER: _AUTH_USER},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# S6: Setup/login flows disabled in proxy mode
# ---------------------------------------------------------------------------


def test_proxy_setup_redirects_to_dashboard(
    proxy_app: TestClient,
) -> None:
    """GET /setup in proxy mode redirects to /."""
    resp = proxy_app.get("/setup", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in ("/", "http://testserver/")


def test_proxy_login_redirects_to_dashboard(
    proxy_app: TestClient,
) -> None:
    """GET /login in proxy mode redirects to /."""
    resp = proxy_app.get("/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in ("/", "http://testserver/")


def test_proxy_logout_redirects_to_dashboard(
    proxy_app: TestClient,
) -> None:
    """POST /logout in proxy mode redirects to / (not /login)."""
    resp = proxy_app.post("/logout", follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = resp.headers["location"]
    assert location in ("/", "http://testserver/")


# ---------------------------------------------------------------------------
# Public paths remain accessible in proxy mode
# ---------------------------------------------------------------------------


def test_proxy_health_check_public(
    proxy_app: TestClient,
) -> None:
    """Health check endpoint is accessible without auth header."""
    resp = proxy_app.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_proxy_health_check_public_untrusted(
    untrusted_proxy_app: TestClient,
) -> None:
    """Health check is accessible even from untrusted IPs."""
    resp = untrusted_proxy_app.get("/api/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Settings page hides Account section in proxy mode
# ---------------------------------------------------------------------------


def test_proxy_settings_hides_account_section(
    proxy_app: TestClient,
) -> None:
    """Settings page in proxy mode does not render the Account section."""
    resp = proxy_app.get(
        "/settings",
        headers={_AUTH_HEADER: _AUTH_USER},
    )
    assert resp.status_code == 200
    # The Account section contains the password change form; it should
    # not be rendered in proxy mode.  CSS/JS may still reference the ID.
    assert b"Manage admin credentials" not in resp.content
    assert b"Update Password" not in resp.content


# ---------------------------------------------------------------------------
# Builtin mode unaffected — smoke test
# ---------------------------------------------------------------------------


def test_builtin_mode_unaffected(app: TestClient) -> None:
    """Default builtin auth mode still redirects to setup as before."""
    resp = app.get("/", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "setup" in location or "login" in location

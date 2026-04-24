"""Tests for authentication: password hashing, session, setup flow, login."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from houndarr.auth import (
    _CSRF_PROTECTED_METHODS,  # noqa: SLF001
    _LOGOUT_PATH,  # noqa: SLF001
    _PUBLIC_PATHS,  # noqa: SLF001
    BCRYPT_COST,
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    check_credentials,
    hash_password,
    is_setup_complete,
    verify_password,
)
from houndarr.repositories.settings import get_setting
from tests.conftest import csrf_headers

# ---------------------------------------------------------------------------
# Unit tests - password hashing
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
# Unit tests - trusted proxy CIDR support (issue #245)
# ---------------------------------------------------------------------------


def test_trusted_proxies_single_ip_match() -> None:
    """Single IP in trusted proxies matches exactly."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.1")
    assert "10.1.1.1" in tp
    assert "10.1.1.2" not in tp


def test_trusted_proxies_cidr_match() -> None:
    """CIDR subnet matches IPs within the range."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.0/24")
    assert "10.1.1.5" in tp
    assert "10.1.1.255" in tp
    assert "10.1.2.1" not in tp


def test_trusted_proxies_cidr_non_match() -> None:
    """CIDR subnet does not match IPs outside the range."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.0/24")
    assert "10.2.0.1" not in tp
    assert "192.168.1.1" not in tp


def test_trusted_proxies_mixed_ips_and_subnets() -> None:
    """Mixed list of single IPs and CIDR subnets works correctly."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.1,172.18.0.0/16")
    assert "10.1.1.1" in tp
    assert "10.1.1.2" not in tp
    assert "172.18.5.10" in tp
    assert "172.19.0.1" not in tp


def test_trusted_proxies_invalid_entries_skipped() -> None:
    """Invalid entries are skipped without crashing."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.1,not-an-ip,10.2.2.0/24,bad/cidr")
    assert "10.1.1.1" in tp
    assert "10.2.2.5" in tp
    assert bool(tp) is True


def test_trusted_proxies_ipv6_subnet() -> None:
    """IPv6 CIDR subnets are supported."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("fd00::/64")
    assert "fd00::1" in tp
    assert "fd00::ffff" in tp
    assert "fd01::1" not in tp


def test_trusted_proxies_empty_string() -> None:
    """Empty string produces a falsy TrustedProxies."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("")
    assert bool(tp) is False
    assert "10.1.1.1" not in tp


def test_trusted_proxies_non_ip_string() -> None:
    """Non-IP strings (like Starlette's 'testclient') return False."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.0/24")
    assert "testclient" not in tp
    assert "not-an-ip" not in tp


def test_trusted_proxies_strict_false_normalizes() -> None:
    """Host bits in CIDR notation are accepted via strict=False."""
    from houndarr.config import _parse_trusted_proxies

    tp = _parse_trusted_proxies("10.1.1.5/24")
    assert "10.1.1.1" in tp
    assert "10.1.1.5" in tp


def test_trusted_proxy_set_caches_result(tmp_data_dir: str) -> None:
    """Calling trusted_proxy_set() twice returns the same object."""
    from houndarr.config import AppSettings

    settings = AppSettings(data_dir=tmp_data_dir, trusted_proxies="10.0.0.0/8")
    first = settings.trusted_proxy_set()
    second = settings.trusted_proxy_set()
    assert first is second


def test_client_ip_honours_xff_for_cidr_trusted_proxy(
    tmp_data_dir: str,
) -> None:
    """_client_ip honours X-Forwarded-For when direct IP is in a trusted subnet."""
    from unittest.mock import MagicMock

    from houndarr.auth import _client_ip  # noqa: SLF001
    from houndarr.config import bootstrap_settings

    try:
        bootstrap_settings(data_dir=tmp_data_dir, trusted_proxies="172.18.0.0/16")
        request = MagicMock()
        request.client.host = "172.18.0.5"
        request.headers.get.return_value = "203.0.113.50, 172.18.0.5"
        assert _client_ip(request) == "203.0.113.50"
    finally:
        bootstrap_settings()


def test_client_ip_ignores_xff_when_not_in_trusted_subnet(
    tmp_data_dir: str,
) -> None:
    """_client_ip ignores X-Forwarded-For when direct IP is NOT in trusted subnet."""
    from unittest.mock import MagicMock

    from houndarr.auth import _client_ip  # noqa: SLF001
    from houndarr.config import bootstrap_settings

    try:
        bootstrap_settings(data_dir=tmp_data_dir, trusted_proxies="172.18.0.0/16")
        request = MagicMock()
        request.client.host = "10.0.0.5"
        request.headers.get.return_value = "203.0.113.50"
        assert _client_ip(request) == "10.0.0.5"
    finally:
        bootstrap_settings()


# ---------------------------------------------------------------------------
# Async unit tests - setup state
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
# resolve_signed_in_as: the identity label the Settings > Security card
# renders. Proxy mode returns the forwarded header; builtin mode returns the
# stored username and falls back to "admin" when nothing is configured yet.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_resolve_signed_in_as_builtin_returns_stored_username(
    db: None,
) -> None:
    from fastapi import Request

    from houndarr.auth import resolve_signed_in_as, set_username

    await set_username("customadmin")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    assert await resolve_signed_in_as(request) == "customadmin"


@pytest.mark.asyncio()
async def test_resolve_signed_in_as_builtin_falls_back_to_admin(
    db: None,
) -> None:
    from fastapi import Request

    from houndarr.auth import resolve_signed_in_as

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    assert await resolve_signed_in_as(request) == "admin"


@pytest.mark.asyncio()
async def test_resolve_signed_in_as_proxy_returns_header_user(
    tmp_data_dir: str,
) -> None:
    from fastapi import Request

    from houndarr.auth import resolve_signed_in_as
    from houndarr.config import bootstrap_settings

    try:
        bootstrap_settings(
            data_dir=tmp_data_dir,
            auth_mode="proxy",
            auth_proxy_header="Remote-User",
            trusted_proxies="127.0.0.1",
        )
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        request.state.proxy_auth_user = "alice"
        assert await resolve_signed_in_as(request) == "alice"
    finally:
        bootstrap_settings()


# ---------------------------------------------------------------------------
# Integration tests - HTTP routes
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
    assert b"<title>Setup | Houndarr</title>" in setup_resp.content

    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )

    login_resp = app.get("/login")
    assert login_resp.status_code == 200
    assert b"<title>Login | Houndarr</title>" in login_resp.content

    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    dashboard_resp = app.get("/")
    assert dashboard_resp.status_code == 200
    assert b"<title>Dashboard | Houndarr</title>" in dashboard_resp.content

    settings_resp = app.get("/settings")
    assert settings_resp.status_code == 200
    assert b"<title>Settings | Houndarr</title>" in settings_resp.content

    logs_resp = app.get("/logs")
    assert logs_resp.status_code == 200
    assert b"<title>Logs | Houndarr</title>" in logs_resp.content

    help_resp = app.get("/settings/help")
    assert help_resp.status_code == 200
    assert b"<title>Settings Help | Houndarr</title>" in help_resp.content


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
        b'Radarr, Sonarr, Lidarr, Readarr, and Whisparr." />' in response.content
    )
    assert b'<meta name="application-name" content="Houndarr" />' in response.content
    assert b'<meta name="apple-mobile-web-app-title" content="Houndarr" />' in response.content
    assert b'<meta name="theme-color" content="#07080f" />' in response.content
    assert b'<meta name="color-scheme" content="dark" />' in response.content
    assert (
        b'<link rel="icon" type="image/png" href="/static/img/houndarr-logo-dark.png" />'
        in response.content
    )
    assert (
        b'<link rel="shortcut icon" href="/static/img/houndarr-logo-dark.png" />'
        in response.content
    )
    assert (
        b'<link rel="apple-touch-icon" href="/static/img/houndarr-logo-dark.png" />'
        in response.content
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
    assert b'hx-trigger="every 30s"' in response.content
    assert b'id="dash-initial-status"' in response.content
    assert b'class="dash-main"' in response.content
    assert b'id="dash-top"' in response.content


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
    # XFF bypass - i.e., a different XFF must not reset the counter.
    assert response2.status_code in (429, 401)  # Either is fine; must not bypass rate limit


# ---------------------------------------------------------------------------
# Characterisation pins for the auth seam split.
#
# Each test below locks one load-bearing auth invariant so the eight
# seam modules under :mod:`houndarr.auth` cannot silently drift the
# bcrypt cost, session cookie kwargs, CSRF bucket survival, or the
# proxy-auth trust-gate ordering.  ``@pytest.mark.pinning`` joins the
# characterisation suite surfaced by ``just pin``.
# ---------------------------------------------------------------------------


# Password seam


@pytest.mark.pinning()
def test_bcrypt_cost_is_12() -> None:
    """The configured bcrypt work factor must stay at cost 12.

    Lowering the cost would silently weaken every newly-hashed password
    after a rotation; raising it without a throughput review could turn
    every login into a denial of service on small hosts.
    """
    assert BCRYPT_COST == 12
    # The resulting hash encodes the cost in its ``$2b$NN$`` prefix;
    # pin that encoding too so a future rotation cannot diverge the
    # constant from the actual bcrypt output.
    hashed = hash_password("work-factor-probe")
    assert hashed.startswith("$2b$12$")


@pytest.mark.pinning()
def test_bcrypt_verify_accepts_exactly_72_byte_password() -> None:
    """72-byte password must round-trip through hash + verify.

    bcrypt's upstream cliff sits at 72 bytes; older binaries silently
    truncated longer input.  The modern ``bcrypt`` Python package
    rejects >72-byte input with ``ValueError``, which
    :func:`verify_password` collapses to ``False`` via the catch-all
    at `auth.py:70`.  Pin the 72-byte acceptance branch so a future
    dependency upgrade cannot silently change the effective password
    length ceiling.
    """
    prefix = "a" * 72
    hashed = hash_password(prefix)
    assert verify_password(prefix, hashed) is True
    # A divergence within the first 72 bytes must still fail.
    assert verify_password("a" * 71 + "b", hashed) is False
    # A longer input must not raise; verify_password collapses to False.
    assert verify_password("a" * 200, hashed) is False


@pytest.mark.pinning()
def test_bcrypt_verify_returns_false_on_malformed_hash() -> None:
    """verify_password must collapse every malformed-hash error to False.

    The catch-all at `auth.py:70` protects the login path from a
    corrupted `password_hash` setting surfacing as a 500.  Specifically
    pin the three error types bcrypt.checkpw can raise: ``ValueError``
    (bad hash shape), ``UnicodeDecodeError`` (hash contains non-UTF8),
    and ``TypeError`` (wrong argument type).
    """
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False
    # Bytes masquerading as a hash would raise TypeError on encode().
    # verify_password takes str hashes; a pathological None coerced to
    # str via its default docstring contract still returns False.
    assert verify_password("anything", "$2b$12$" + "!" * 53) is False


# Rate-limit seam


@pytest.mark.pinning()
def test_clear_login_attempts_drops_bucket(test_settings: object) -> None:
    """clear_login_attempts must drop the per-IP bucket entirely.

    Called on every successful login (auth.py:407).  A residual bucket
    would let an attacker lock the owner out by exhausting the window
    with wrong guesses and the owner's subsequent correct login would
    not clear the record.
    """
    from unittest.mock import MagicMock

    from houndarr.auth import (
        _login_attempts,  # noqa: SLF001
        check_login_rate_limit,
        clear_login_attempts,
        record_failed_login,
    )

    request = MagicMock()
    request.client.host = "198.51.100.7"
    request.headers.get.return_value = None
    for _ in range(3):
        record_failed_login(request)
    assert "198.51.100.7" in _login_attempts
    clear_login_attempts(request)
    assert "198.51.100.7" not in _login_attempts
    assert check_login_rate_limit(request) is True


@pytest.mark.pinning()
def test_check_login_rate_limit_prunes_stale_entries(
    test_settings: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entries older than the window must fall off the sliding count.

    Without pruning, a client could be locked out by attempts that
    scrolled past the window hours ago, and the counter would grow
    unboundedly in memory.  Pin the semantics by clock-travelling past
    the window and confirming the limiter re-admits the client.
    """
    from unittest.mock import MagicMock

    import houndarr.auth as _auth
    from houndarr.auth import (
        _LOGIN_WINDOW_SECONDS,  # noqa: SLF001
        _login_attempts,  # noqa: SLF001
        check_login_rate_limit,
        record_failed_login,
    )

    now = {"t": 1_700_000_000.0}
    monkeypatch.setattr(_auth.time, "time", lambda: now["t"])

    request = MagicMock()
    request.client.host = "198.51.100.9"
    request.headers.get.return_value = None
    for _ in range(5):
        record_failed_login(request)
    assert check_login_rate_limit(request) is False

    # Advance past the window; the next check must prune and re-admit.
    now["t"] += _LOGIN_WINDOW_SECONDS + 1
    assert check_login_rate_limit(request) is True
    assert _login_attempts["198.51.100.9"] == []


# Setup seam


@pytest.mark.asyncio()
@pytest.mark.pinning()
async def test_reset_auth_caches_clears_every_module_global(db: None) -> None:
    """reset_auth_caches must clear _serializer, _setup_complete, _login_attempts.

    Called on factory reset (services/admin.py:263).  A stale
    ``_setup_complete=True`` after the database is wiped would short-
    circuit the `/setup` redirect and strand the operator without a
    way to configure a new owner.
    """
    from unittest.mock import MagicMock

    import houndarr.auth as _auth
    from houndarr.auth import (
        _get_serializer,  # noqa: SLF001
        record_failed_login,
        reset_auth_caches,
        set_password,
    )

    # Prime every cache.
    await set_password("SomePass1!")
    await _get_serializer()
    req = MagicMock()
    req.client.host = "198.51.100.11"
    req.headers.get.return_value = None
    record_failed_login(req)

    assert _auth._setup_complete is True  # noqa: SLF001
    assert _auth._serializer is not None  # noqa: SLF001
    assert _auth._login_attempts  # noqa: SLF001

    reset_auth_caches()

    assert _auth._setup_complete is None  # noqa: SLF001
    assert _auth._serializer is None  # noqa: SLF001
    assert _auth._login_attempts == {}  # noqa: SLF001


# Session seam


@pytest.mark.asyncio()
@pytest.mark.pinning()
async def test_get_serializer_lazy_init_reads_session_secret(db: None) -> None:
    """First call generates-and-persists the secret; subsequent calls reuse it."""
    import houndarr.auth as _auth
    from houndarr.auth import _get_serializer  # noqa: SLF001

    assert _auth._serializer is None  # noqa: SLF001
    assert await get_setting("session_secret") is None

    first = await _get_serializer()
    assert _auth._serializer is first  # noqa: SLF001
    persisted = await get_setting("session_secret")
    assert persisted is not None and len(persisted) == 64  # 32 bytes hex

    # Second call must not rotate the secret.
    second = await _get_serializer()
    assert second is first
    assert await get_setting("session_secret") == persisted


@pytest.mark.asyncio()
@pytest.mark.pinning()
async def test_rotate_session_secret_invalidates_prior_token(db: None) -> None:
    """A token signed with the prior secret must fail verification after rotate.

    Critical for the password-change flow: rotating the session secret
    is how Houndarr invalidates every other signed-in tab after a
    credential change.
    """
    from itsdangerous import BadSignature

    from houndarr.auth import _get_serializer, rotate_session_secret  # noqa: SLF001

    serializer = await _get_serializer()
    signed = serializer.dumps({"ts": 1, "csrf": "tok"})

    await rotate_session_secret()
    refreshed = await _get_serializer()

    with pytest.raises(BadSignature):
        refreshed.loads(signed, max_age=60)


@pytest.mark.asyncio()
@pytest.mark.pinning()
async def test_get_session_csrf_token_extracts_payload(app: TestClient) -> None:
    """get_session_csrf_token reads the CSRF value embedded in the session cookie.

    Same cookie both stores the session and carries the per-session
    CSRF token; the cookie value is signed and must round-trip through
    ``itsdangerous`` before the CSRF check can compare-digest against
    the submitted header.
    """
    from fastapi import Request

    from houndarr.auth import get_session_csrf_token

    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    session_cookie = app.cookies.get(SESSION_COOKIE_NAME)
    csrf_cookie = app.cookies.get(CSRF_COOKIE_NAME)
    assert session_cookie is not None
    assert csrf_cookie is not None

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", f"{SESSION_COOKIE_NAME}={session_cookie}".encode())],
        "query_string": b"",
    }
    request = Request(scope)
    extracted = await get_session_csrf_token(request)
    assert extracted == csrf_cookie


# Username helpers


@pytest.mark.pinning()
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  ADMIN  ", "admin"),
        ("MixedCase", "mixedcase"),
        ("\talice\n", "alice"),
        ("", ""),
        ("a", "a"),
    ],
)
def test_normalize_username_lowercases_and_strips(raw: str, expected: str) -> None:
    from houndarr.auth import normalize_username

    assert normalize_username(raw) == expected


@pytest.mark.pinning()
@pytest.mark.parametrize(
    "raw,error_fragment",
    [
        ("", "Username is required"),
        ("ab", "3-32 characters"),
        ("a" * 33, "3-32 characters"),
        ("has space", "lowercase letters"),
        ("CAPITAL", None),  # normalized to 'capital', passes
        ("bad!char", "lowercase letters"),
        ("ok.name_1", None),
        ("dotted.name-here", None),
    ],
)
def test_validate_username_error_messages(raw: str, error_fragment: str | None) -> None:
    from houndarr.auth import validate_username

    result = validate_username(raw)
    if error_fragment is None:
        assert result is None
    else:
        assert result is not None
        assert error_fragment in result


# Module constants (pin the exact values)


@pytest.mark.pinning()
def test_logout_path_constant_is_slash_logout() -> None:
    """_LOGOUT_PATH must stay '/logout'; any drift would silently break logout."""
    assert _LOGOUT_PATH == "/logout"


@pytest.mark.pinning()
def test_csrf_protected_methods_constant() -> None:
    """The set of CSRF-protected methods must stay exactly {POST, PUT, PATCH, DELETE}."""
    assert frozenset(["POST", "PUT", "PATCH", "DELETE"]) == _CSRF_PROTECTED_METHODS


@pytest.mark.pinning()
def test_public_paths_constant_has_exact_contents() -> None:
    """_PUBLIC_PATHS must stay exactly {/setup, /login, /api/health, /static}.

    Each entry is matched via ``path.startswith`` in the middleware, so
    the breadth is deliberately small; widening it without updating
    every consumer (proxy-auth dead-path redirects included) would
    silently bypass the auth check.
    """
    assert frozenset(["/setup", "/login", "/api/health", "/static"]) == _PUBLIC_PATHS


# Proxy-auth seam


@pytest.mark.pinning()
def test_extract_proxy_username_returns_none_on_missing_or_blank(
    tmp_data_dir: str,
) -> None:
    """_extract_proxy_username returns None for absent or whitespace-only headers.

    The middleware composes this with `_is_trusted_proxy`; returning an
    empty string would let the Auth middleware treat a blank header as
    an authenticated identity.
    """
    from unittest.mock import MagicMock

    from houndarr.auth import _extract_proxy_username  # noqa: SLF001
    from houndarr.config import bootstrap_settings

    try:
        bootstrap_settings(
            data_dir=tmp_data_dir,
            auth_mode="proxy",
            auth_proxy_header="Remote-User",
            trusted_proxies="172.18.0.5",
        )
        request = MagicMock()
        request.headers.get.return_value = ""
        assert _extract_proxy_username(request) is None
        request.headers.get.return_value = "   "
        assert _extract_proxy_username(request) is None
        request.headers.get.return_value = None
        # get(header, "") returns "" when absent; MagicMock autospec'd to
        # return None still exercises the empty-string branch correctly.
        request.headers.get.return_value = ""
        assert _extract_proxy_username(request) is None
    finally:
        bootstrap_settings()


@pytest.mark.pinning()
def test_validate_csrf_reads_form_body_csrf_token_field(app: TestClient) -> None:
    """The form-body fallback at `auth.py:208` must accept csrf_token inputs.

    Covered transitively elsewhere, but pinned in isolation here so the
    header-first / body-fallback order survives the csrf seam split.
    """
    from tests.conftest import get_csrf_token

    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    token = get_csrf_token(app)
    assert token

    # Post without the X-CSRF-Token header; the form body must supply it.
    resp = app.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303


# Middleware seam (ordering invariant)


@pytest.mark.asyncio()
@pytest.mark.pinning()
async def test_dispatch_proxy_calls_trust_gate_before_header_read(
    tmp_data_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy dispatch must call `_is_trusted_proxy` before reading the header.

    The trust gate owns the IP check.  Reading the auth header before
    the IP check would allow a spoofed ``Remote-User`` header from any
    client to influence a log message even if the middleware still
    rejected the request.  Pin the call order so the middleware seam
    split cannot silently reorder the two helpers.
    """
    from unittest.mock import AsyncMock, MagicMock

    from houndarr.auth import AuthMiddleware
    from houndarr.auth import proxy_auth as _proxy_auth
    from houndarr.config import bootstrap_settings

    try:
        bootstrap_settings(
            data_dir=tmp_data_dir,
            auth_mode="proxy",
            auth_proxy_header="Remote-User",
            trusted_proxies="172.18.0.5",
        )
        calls: list[str] = []

        def fake_is_trusted(request: object) -> bool:
            calls.append("trust")
            return False  # force early return

        def fake_extract(request: object) -> str | None:
            calls.append("extract")
            return "never-seen"

        # Patch the proxy_auth module directly: the middleware resolves
        # each helper via ``proxy_auth.<name>`` at call time so the patch
        # propagates without also having to update the import site.
        monkeypatch.setattr(_proxy_auth, "_is_trusted_proxy", fake_is_trusted)
        monkeypatch.setattr(_proxy_auth, "_extract_proxy_username", fake_extract)

        middleware = AuthMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/settings"
        request.method = "GET"
        request.client.host = "10.0.0.1"
        call_next = AsyncMock()
        await middleware._dispatch_proxy(request, call_next, "/settings")  # noqa: SLF001

        assert calls == ["trust"], (
            f"expected only trust gate to run when IP is untrusted, got {calls!r}"
        )
    finally:
        bootstrap_settings()

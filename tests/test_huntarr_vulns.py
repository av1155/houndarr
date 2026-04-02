"""Regression tests proving Houndarr is not vulnerable to the 20 security
findings from the Huntarr v9.4.2 security review.

Each class maps to one or more Huntarr vulnerability IDs (VUL-01 through
VUL-20) documented at https://github.com/rfsbraz/huntarr-security-review.
Tests are organised by attack category rather than VUL number so related
assertions live together.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi.testclient import TestClient

import houndarr.auth as _auth_module
from houndarr.auth import (
    _PUBLIC_PATHS,  # noqa: SLF001
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
)
from houndarr.clients.base import ArrClient
from tests.conftest import csrf_headers

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_AUTH_LOCATIONS = {"/setup", "/login", "http://testserver/setup", "http://testserver/login"}

# Patterns that must never appear in any HTTP response body.
# Excludes "password" intentionally: the word appears in login form labels.
_SENSITIVE_PATTERNS = ("api_key", "gAAAAA", "masterkey", "fernet")

# Minimal valid form data for creating a test instance.
# The URL uses a Docker-style hostname that passes SSRF validation
# (no blocked ranges; DNS failure is deferred to the connectivity test).
_VALID_INSTANCE_FORM: dict[str, str] = {
    "name": "Test Radarr",
    "type": "radarr",
    "url": "http://radarr:7878",
    "api_key": "test-api-key-abc123",
    "connection_verified": "true",
}


def _login(client: TestClient) -> None:
    """Complete setup + login so subsequent requests are authenticated."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


def _complete_setup(client: TestClient) -> None:
    """Complete setup without logging in (for tests that need setup done but no session)."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )


# ---------------------------------------------------------------------------
# VUL-01, VUL-02, VUL-03: Every protected route requires authentication
# ---------------------------------------------------------------------------


class TestUnauthenticatedAccessSweep:
    """Every protected route must redirect unauthenticated requests (VUL-01, VUL-02, VUL-03).

    Huntarr had multiple endpoints reachable without any session, including
    settings writes, credential endpoints, and account management routes.
    Houndarr's AuthMiddleware intercepts all such requests before they reach
    route handlers.
    """

    _PROTECTED_ROUTES = [
        ("GET", "/"),
        ("GET", "/logs"),
        ("GET", "/settings/help"),
        ("GET", "/settings"),
        ("GET", "/settings/instances/add-form"),
        ("GET", "/settings/instances/1/edit"),
        ("POST", "/settings/account/password"),
        ("POST", "/settings/instances/test-connection"),
        ("POST", "/settings/instances"),
        ("POST", "/settings/instances/1"),
        ("POST", "/settings/instances/1/toggle-enabled"),
        ("POST", "/api/instances/1/run-now"),
        ("DELETE", "/settings/instances/1"),
        ("GET", "/api/status"),
        ("GET", "/api/logs"),
        ("GET", "/api/logs/partial"),
    ]

    @pytest.mark.parametrize("method,path", _PROTECTED_ROUTES)
    def test_protected_routes_redirect_without_session(
        self,
        app: TestClient,
        method: str,
        path: str,
    ) -> None:
        """Protected routes redirect to setup or login without a valid session."""
        resp = app.request(method, path, follow_redirects=False)
        assert resp.status_code in {302, 307}, (
            f"{method} {path} returned {resp.status_code}; expected redirect to auth"
        )
        assert resp.headers.get("location") in _AUTH_LOCATIONS, (
            f"{method} {path} redirected to unexpected location: {resp.headers.get('location')!r}"
        )


# ---------------------------------------------------------------------------
# VUL-01, VUL-11: No secrets in unauthenticated responses
# ---------------------------------------------------------------------------


class TestPublicResponseSecretLeakage:
    """Public endpoint responses must never contain sensitive data (VUL-01, VUL-11).

    Huntarr's /api/settings/general returned cleartext API keys for all
    connected *arr apps without any authentication. Houndarr's four public
    paths must return only the minimum information required.
    """

    def test_health_returns_exactly_status_ok(self, app: TestClient) -> None:
        """/api/health must return exactly {"status": "ok"} with no extra fields."""
        resp = app.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.parametrize("path", ["/api/health", "/login", "/setup"])
    def test_public_paths_contain_no_sensitive_patterns(
        self,
        app: TestClient,
        path: str,
    ) -> None:
        """Public responses must not contain api_key, Fernet tokens, or internal secrets."""
        resp = app.get(path)
        body_lower = resp.text.lower()
        for pattern in _SENSITIVE_PATTERNS:
            assert pattern.lower() not in body_lower, (
                f"GET {path} response contained sensitive pattern {pattern!r}"
            )


# ---------------------------------------------------------------------------
# VUL-07, T3: Setup endpoint locked after completion; setup_mode bypass blocked
# ---------------------------------------------------------------------------


class TestSetupLockAndBypass:
    """Setup is inaccessible after initial account creation (VUL-07, T3).

    Huntarr had a /api/setup/clear endpoint that re-armed account creation
    without authentication, and also trusted a client-supplied setup_mode
    parameter to skip auth checks (VUL-02). Houndarr prevents both: /setup
    locks once a password is stored, and no body parameter influences whether
    the middleware enforces authentication.
    """

    def test_setup_get_redirects_after_completion(self, app: TestClient) -> None:
        """GET /setup redirects to /login once setup is complete."""
        _complete_setup(app)
        resp = app.get("/setup", follow_redirects=False)
        assert resp.status_code in {302, 307}
        assert "/login" in resp.headers.get("location", "")

    def test_setup_post_cannot_create_second_account(self, app: TestClient) -> None:
        """POST /setup with new credentials after setup is complete must be rejected.

        Verifies that a second owner account cannot be created by re-submitting
        the setup form, and that the original credentials remain valid.
        """
        _complete_setup(app)
        resp = app.post(
            "/setup",
            data={
                "username": "hacker",
                "password": "HackerPass1!",
                "password_confirm": "HackerPass1!",
            },
            follow_redirects=False,
        )
        assert resp.status_code in {302, 307}
        # Original credentials still authenticate; no second account created.
        login_resp = app.post(
            "/login",
            data={"username": "admin", "password": "ValidPass1!"},
            follow_redirects=False,
        )
        assert login_resp.status_code in {302, 303}

    def test_setup_mode_body_field_does_not_bypass_auth(self, app: TestClient) -> None:
        """A setup_mode field in the POST body must not bypass authentication.

        Huntarr trusted a client-supplied setup_mode parameter to skip auth
        checks (VUL-02). Houndarr's AuthMiddleware runs before routing, so
        no body field can influence whether a session is required.
        """
        _complete_setup(app)
        resp = app.post(
            "/settings/instances",
            data={
                "setup_mode": "true",
                "name": "Evil Instance",
                "type": "radarr",
                "url": "http://radarr:7878",
                "api_key": "evil-key",
            },
            follow_redirects=False,
        )
        assert resp.status_code in {302, 307}
        assert resp.headers.get("location") in _AUTH_LOCATIONS


# ---------------------------------------------------------------------------
# VUL-03, VUL-04, VUL-05, VUL-06, VUL-07: Dangerous features do not exist
# ---------------------------------------------------------------------------


class TestNoDangerousEndpoints:
    """Routes present in Huntarr that enabled critical attacks do not exist (VUL-03 to VUL-07).

    These features are absent by design: no Plex integration, no 2FA, no
    backup/restore, no recovery keys, no setup clear endpoint. All these
    paths must return 404 from an authenticated session.
    """

    # POST paths from Huntarr's critical finding chain; all must be 404 here.
    _HUNTARR_POST_PATHS = [
        "/api/auth/plex/link",
        "/api/auth/plex/unlink",
        "/api/user/2fa/setup",
        "/api/user/2fa/verify",
        "/api/setup/clear",
        "/api/backup/restore",
        "/api/backup/upload",
        "/auth/recovery-key/generate",
    ]

    @pytest.fixture(autouse=True)
    def _mock_ping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prevent real network calls during instance creation in this class."""

        async def _always_ok(self: ArrClient) -> dict[str, Any] | None:
            name = type(self).__name__.replace("Client", "")
            return {"appName": name, "version": "4.0.0"}

        monkeypatch.setattr(ArrClient, "ping", _always_ok)

    @pytest.mark.parametrize("path", _HUNTARR_POST_PATHS)
    def test_huntarr_post_paths_return_404(
        self,
        app: TestClient,
        path: str,
    ) -> None:
        """Huntarr-specific POST paths must not be registered in Houndarr."""
        _login(app)
        resp = app.post(path, headers=csrf_headers(app))
        assert resp.status_code == 404, (
            f"POST {path} returned {resp.status_code}; this endpoint must not exist"
        )

    def test_backup_delete_returns_404(self, app: TestClient) -> None:
        """DELETE /api/backup (Huntarr VUL-12 path traversal vector) must not exist."""
        _login(app)
        resp = app.delete("/api/backup", headers=csrf_headers(app))
        assert resp.status_code == 404

    def test_no_zip_file_upload_endpoint(self, app: TestClient) -> None:
        """No endpoint should accept multipart zip uploads (VUL-06: zip slip).

        Huntarr's backup restore endpoint called zipfile.extractall() without
        path sanitisation, enabling arbitrary file write as root. Houndarr has
        no backup feature and no file upload endpoints.
        """
        _login(app)
        for path in ["/api/backup/restore", "/backup/restore", "/api/backup/upload"]:
            resp = app.post(
                path,
                files={"file": ("backup.zip", b"PK\x03\x04", "application/zip")},
                headers=csrf_headers(app),
            )
            assert resp.status_code in {404, 405}, (
                f"POST {path} with zip upload returned {resp.status_code}; "
                "expected 404 (no such endpoint)"
            )


# ---------------------------------------------------------------------------
# VUL-12: Path traversal never returns 200
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """URL path traversal attempts must not produce a successful 200 response (VUL-12).

    Huntarr's backup delete endpoint concatenated a user-supplied backup_id
    with the backup directory path without sanitisation. Houndarr has no
    backup feature; Starlette also normalises URL paths before routing.
    """

    _TRAVERSAL_PATHS = [
        "/../../../etc/passwd",
        "/static/../../etc/passwd",
        "/api/logs/../../../etc/passwd",
        "/settings/../../../etc/passwd",
    ]

    @pytest.mark.parametrize("path", _TRAVERSAL_PATHS)
    def test_traversal_path_does_not_return_200(
        self,
        app: TestClient,
        path: str,
    ) -> None:
        """Path traversal attempts must not produce a 200 OK response."""
        resp = app.get(path, follow_redirects=False)
        assert resp.status_code != 200, (
            f"GET {path} returned 200; path traversal may have succeeded"
        )


# ---------------------------------------------------------------------------
# VUL-08, VUL-15: X-Forwarded-For cannot spoof auth or rate limit
# ---------------------------------------------------------------------------


class TestXFFSpoofingBlocked:
    """X-Forwarded-For is ignored without a trusted proxy configured (VUL-08, VUL-15).

    Huntarr read X-Forwarded-For directly for its local-access bypass,
    allowing any client to spoof a trusted IP. Houndarr only honours XFF
    when the direct connection IP is in HOUNDARR_TRUSTED_PROXIES (empty by
    default in test_settings).
    """

    def test_xff_127_does_not_bypass_auth(self, app: TestClient) -> None:
        """X-Forwarded-For: 127.0.0.1 must not bypass auth without trusted proxies set."""
        _complete_setup(app)
        resp = app.get(
            "/",
            headers={"X-Forwarded-For": "127.0.0.1"},
            follow_redirects=False,
        )
        assert resp.status_code in {302, 307}
        assert "/login" in resp.headers.get("location", "")

    def test_xff_variation_does_not_reset_rate_limit(self, app: TestClient) -> None:
        """Changing the XFF header per request must not reset the rate-limit counter.

        Huntarr keyed its rate limiter on the XFF value, so rotating IPs
        bypassed the lockout threshold. Houndarr keys on the direct
        connection IP, which the test client cannot vary.
        """
        _complete_setup(app)
        # Five failed logins from the direct test-client IP saturate the counter.
        for i in range(5):
            app.post(
                "/login",
                data={"username": "admin", "password": "WrongPassword!"},
                headers={"X-Forwarded-For": f"10.0.0.{i + 1}"},
            )
        # Sixth attempt must be rate-limited regardless of the XFF value.
        resp = app.post(
            "/login",
            data={"username": "admin", "password": "WrongPassword!"},
            headers={"X-Forwarded-For": "192.168.1.99"},
            follow_redirects=False,
        )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# VUL-01, VUL-11, T5: API keys never appear in any HTTP response
# ---------------------------------------------------------------------------


class TestAPIKeyNeverExposed:
    """Encrypted API keys must never appear in any HTTP response body (VUL-01, VUL-11).

    Huntarr's /api/settings/general returned cleartext API keys for all
    connected *arr apps. Houndarr encrypts keys with Fernet at rest and
    uses an __UNCHANGED__ sentinel in edit forms so the real key value is
    never serialised into any response.
    """

    @pytest.fixture(autouse=True)
    def _mock_ping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prevent real network calls for instance creation in this class."""

        async def _always_ok(self: ArrClient) -> dict[str, Any] | None:
            name = type(self).__name__.replace("Client", "")
            return {"appName": name, "version": "4.0.0"}

        monkeypatch.setattr(ArrClient, "ping", _always_ok)

    def test_api_status_has_no_api_key_field(self, app: TestClient) -> None:
        """/api/status JSON must not include an api_key field for any instance."""
        _login(app)
        resp = app.get("/api/status")
        assert resp.status_code == 200
        items = resp.json()
        assert isinstance(items, list)
        for item in items:
            assert "api_key" not in item, (
                f"Instance {item.get('name')!r} exposed api_key in /api/status"
            )

    def test_settings_page_contains_no_fernet_prefix(self, app: TestClient) -> None:
        """/settings HTML must not contain a gAAAAA-prefixed Fernet token.

        Fernet-encrypted values always begin with gAAAAA (base64 encoding of
        the 0x80 version byte). Their presence would indicate encrypted_api_key
        is being rendered directly from the database row.
        """
        _login(app)
        app.post("/settings/instances", data=_VALID_INSTANCE_FORM, headers=csrf_headers(app))
        resp = app.get("/settings")
        assert resp.status_code == 200
        assert "gAAAAA" not in resp.text

    def test_instance_edit_form_uses_unchanged_sentinel(self, app: TestClient) -> None:
        """The instance edit form must pre-fill __UNCHANGED__ rather than the real API key."""
        _login(app)
        app.post("/settings/instances", data=_VALID_INSTANCE_FORM, headers=csrf_headers(app))
        resp = app.get("/settings/instances/1/edit")
        assert resp.status_code == 200
        assert "__UNCHANGED__" in resp.text
        assert "gAAAAA" not in resp.text
        assert "test-api-key-abc123" not in resp.text

    def test_api_logs_contains_no_api_key_or_fernet_token(self, app: TestClient) -> None:
        """/api/logs JSON must not contain api_key fields or Fernet-encrypted values."""
        _login(app)
        resp = app.get("/api/logs")
        assert resp.status_code == 200
        body = resp.text
        assert "api_key" not in body
        assert "gAAAAA" not in body


# ---------------------------------------------------------------------------
# Session and CSRF cookie security attributes
# ---------------------------------------------------------------------------


class TestCookieSecurityFlags:
    """Session and CSRF cookies must carry the correct security attributes.

    HttpOnly prevents JS from reading the session token (XSS mitigation).
    SameSite blocks cross-origin requests (CSRF mitigation layer).  The
    default is ``lax``, which allows top-level GET navigations from external
    links (dashboard apps, bookmarks) while blocking cross-site form
    submissions.  Users may override to ``strict`` via
    ``HOUNDARR_COOKIE_SAMESITE``.
    The CSRF cookie must NOT be HttpOnly because HTMX reads it from JS to
    include in the X-CSRF-Token request header.
    """

    def _login_response_cookies(self, app: TestClient) -> list[str]:
        """Return all Set-Cookie header values from a successful login response."""
        _complete_setup(app)
        resp = app.post(
            "/login",
            data={"username": "admin", "password": "ValidPass1!"},
            follow_redirects=False,
        )
        return resp.headers.get_list("set-cookie")

    def test_session_cookie_is_httponly(self, app: TestClient) -> None:
        """houndarr_session cookie must have HttpOnly to prevent JS access."""
        cookies = self._login_response_cookies(app)
        session = next((c for c in cookies if SESSION_COOKIE_NAME in c), None)
        assert session is not None, "houndarr_session not found in login Set-Cookie headers"
        assert "httponly" in session.lower()

    def test_session_cookie_has_samesite_lax(self, app: TestClient) -> None:
        """houndarr_session cookie must have SameSite=lax (default)."""
        cookies = self._login_response_cookies(app)
        session = next((c for c in cookies if SESSION_COOKIE_NAME in c), None)
        assert session is not None
        assert "samesite=lax" in session.lower()

    def test_session_cookie_has_24h_max_age(self, app: TestClient) -> None:
        """houndarr_session cookie must expire after SESSION_MAX_AGE_SECONDS (86400 = 24h)."""
        cookies = self._login_response_cookies(app)
        session = next((c for c in cookies if SESSION_COOKIE_NAME in c), None)
        assert session is not None
        assert f"max-age={SESSION_MAX_AGE_SECONDS}" in session.lower()

    def test_csrf_cookie_is_not_httponly(self, app: TestClient) -> None:
        """houndarr_csrf cookie must NOT be HttpOnly so HTMX can read it from JS."""
        cookies = self._login_response_cookies(app)
        csrf = next((c for c in cookies if CSRF_COOKIE_NAME in c), None)
        assert csrf is not None, "houndarr_csrf not found in login Set-Cookie headers"
        assert "httponly" not in csrf.lower()

    def test_csrf_cookie_has_samesite_lax(self, app: TestClient) -> None:
        """houndarr_csrf cookie must have SameSite=lax (default)."""
        cookies = self._login_response_cookies(app)
        csrf = next((c for c in cookies if CSRF_COOKIE_NAME in c), None)
        assert csrf is not None
        assert "samesite=lax" in csrf.lower()


# ---------------------------------------------------------------------------
# VUL-13 analog: CSRF implementation quality and whitelist exactness
# ---------------------------------------------------------------------------


class TestCSRFImplementation:
    """CSRF implementation must use constant-time comparison and an exact allowlist (VUL-13).

    Huntarr used substring matching for its auth bypass whitelist (e.g.,
    '/api/user/2fa/' in path), which caused route creep: new routes under
    those prefixes were accidentally made public. Houndarr uses a frozen
    set of four exact prefixes and constant-time token comparison.
    """

    def test_public_paths_is_exact_expected_set(self) -> None:
        """_PUBLIC_PATHS must contain exactly the four expected path prefixes."""
        expected = frozenset(["/setup", "/login", "/api/health", "/static"])
        assert expected == _PUBLIC_PATHS, (
            f"_PUBLIC_PATHS changed: got {_PUBLIC_PATHS!r}, expected {expected!r}"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "/setupextra",
            "/loginextra",
            "/api/healthcheck",
            "/staticfiles",
            "/setup2",
            "/api/health/extra",
        ],
    )
    def test_near_miss_paths_return_404_over_http(
        self,
        app: TestClient,
        path: str,
    ) -> None:
        """Near-miss paths that match the startswith whitelist must still return 404.

        The startswith matching means /setupextra matches /setup in the allowlist.
        That is safe only because no route handler exists for those paths.
        This test verifies that at the HTTP level they return 404 rather than
        serving any protected or unprotected content, ensuring whitelist breadth
        does not grant access to unintended resources.
        """
        resp = app.get(path, follow_redirects=False)
        assert resp.status_code == 404, (
            f"GET {path} returned {resp.status_code}; a near-miss public path matched a real route"
        )

    def test_validate_csrf_uses_compare_digest(self) -> None:
        """validate_csrf must use hmac.compare_digest for constant-time comparison.

        Non-constant-time comparison (==) can leak token length via timing.
        This test inspects the source of validate_csrf to assert compare_digest
        is called rather than a plain equality check.
        """
        source = inspect.getsource(_auth_module.validate_csrf)
        assert "compare_digest" in source, (
            "validate_csrf does not call compare_digest; "
            "replace with constant-time comparison to prevent timing attacks"
        )


# ---------------------------------------------------------------------------
# CSRF enforcement: every mutating route returns 403 without a valid token
# ---------------------------------------------------------------------------


class TestCSRFEnforcement:
    """Every mutating authenticated route must return 403 without a valid CSRF token.

    The AuthMiddleware checks CSRF before calling the route handler, so even
    requests with partial or missing form data return 403 rather than a
    validation error, provided the session is valid.
    """

    _MUTATING_ROUTES = [
        ("POST", "/settings/account/password"),
        ("POST", "/settings/instances/test-connection"),
        ("POST", "/settings/instances"),
        ("POST", "/settings/instances/1"),
        ("POST", "/settings/instances/1/toggle-enabled"),
        ("POST", "/api/instances/1/run-now"),
        ("DELETE", "/settings/instances/1"),
    ]

    @pytest.mark.parametrize("method,path", _MUTATING_ROUTES)
    def test_mutating_route_requires_csrf_token(
        self,
        app: TestClient,
        method: str,
        path: str,
    ) -> None:
        """Authenticated POST/DELETE without X-CSRF-Token must return 403."""
        _login(app)
        # No CSRF header, no csrf_token form field.
        resp = app.request(method, path)
        assert resp.status_code == 403, (
            f"{method} {path} returned {resp.status_code} without CSRF token; expected 403"
        )
        assert b"CSRF token invalid or missing" in resp.content

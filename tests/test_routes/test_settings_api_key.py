"""Tests for the Houndarr API key admin sub-section routes.

The API key surface is rendered inline within the Settings page's Admin
parent dropdown.  GET /settings is responsible for the section's initial
state; the two POST routes (generate, revoke) return the section partial
so HTMX can swap ``#admin-api-key`` in place.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

import houndarr.auth as _auth_mod
from houndarr.auth.houndarr_api_key import hash_api_key
from houndarr.config import bootstrap_settings
from houndarr.repositories import widget_api_key
from tests.conftest import csrf_headers

_AUTH_LOCATIONS = {"/setup", "/login", "http://testserver/setup", "http://testserver/login"}
_AUTH_HEADER = "Remote-User"
_AUTH_USER = "alice"
_TOKEN_RE = re.compile(rb"hndarr_[A-Za-z0-9_-]+")
_UNTRUSTED_IP = "1.2.3.4"


def _login(client: TestClient) -> None:
    """Complete setup and login so subsequent requests are authenticated."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


def _extract_plaintext_key(response_content: bytes) -> str:
    match = _TOKEN_RE.search(response_content)
    assert match is not None
    return match.group(0).decode("ascii")


def _generate_key(client: TestClient) -> str:
    response = client.post(
        "/settings/api-key/generate",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    return _extract_plaintext_key(response.content)


@pytest.fixture()
def untrusted_proxy_app(tmp_data_dir: str) -> Generator[TestClient]:
    """Return a proxy-mode client that simulates a direct connection."""
    bootstrap_settings(
        data_dir=tmp_data_dir,
        auth_mode="proxy",
        auth_proxy_header=_AUTH_HEADER,
        trusted_proxies="172.18.0.5",
    )
    _auth_mod.reset_auth_caches()

    from houndarr.app import create_app

    application = create_app()
    original_app = application

    async def _patched_app(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http":
            scope["client"] = (_UNTRUSTED_IP, 0)
        await original_app(scope, receive, send)

    with TestClient(_patched_app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def trusted_proxy_app(tmp_data_dir: str) -> Generator[TestClient]:
    """Return a proxy-mode client that simulates an authenticated proxy."""
    trusted_ip = "172.18.0.5"
    bootstrap_settings(
        data_dir=tmp_data_dir,
        auth_mode="proxy",
        auth_proxy_header=_AUTH_HEADER,
        trusted_proxies=trusted_ip,
    )
    _auth_mod.reset_auth_caches()

    from houndarr.app import create_app

    application = create_app()
    original_app = application

    async def _patched_app(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http":
            scope["client"] = (trusted_ip, 0)
        await original_app(scope, receive, send)

    with TestClient(_patched_app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Auth lane: POST routes inherit the global auth middleware.
# ---------------------------------------------------------------------------


def test_api_key_posts_require_builtin_auth(app: TestClient) -> None:
    """Unauthenticated POSTs redirect to /setup or /login in builtin mode."""
    generate_response = app.post("/settings/api-key/generate", follow_redirects=False)
    assert generate_response.status_code == 302
    assert generate_response.headers["location"] in _AUTH_LOCATIONS

    revoke_response = app.post("/settings/api-key/revoke", follow_redirects=False)
    assert revoke_response.status_code == 302
    assert revoke_response.headers["location"] in _AUTH_LOCATIONS


def test_api_key_posts_block_direct_proxy_connections(
    untrusted_proxy_app: TestClient,
) -> None:
    """Proxy mode with an untrusted source IP rejects POSTs with 403."""
    generate_response = untrusted_proxy_app.post(
        "/settings/api-key/generate",
        follow_redirects=False,
    )
    assert generate_response.status_code == 403


def test_api_key_posts_allow_trusted_proxy_auth(
    trusted_proxy_app: TestClient,
) -> None:
    """Proxy mode with a trusted source IP plus auth header succeeds."""
    # Prime the CSRF cookie: in proxy mode the auth middleware sets the
    # token on the first proxy-authenticated request.  Without this prime
    # the POST below would 403 before reaching the route.
    prime = trusted_proxy_app.get("/settings", headers={_AUTH_HEADER: _AUTH_USER})
    assert prime.status_code == 200

    response = trusted_proxy_app.post(
        "/settings/api-key/generate",
        headers={_AUTH_HEADER: _AUTH_USER, **csrf_headers(trusted_proxy_app)},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert b'id="admin-api-key"' in response.content


def test_api_key_posts_require_csrf(db: None, app: TestClient) -> None:
    """Authenticated POSTs without a CSRF token return 403."""
    _login(app)

    generate_response = app.post("/settings/api-key/generate")
    revoke_response = app.post("/settings/api-key/revoke")

    assert generate_response.status_code == 403
    assert revoke_response.status_code == 403


# ---------------------------------------------------------------------------
# Section rendering: GET /settings includes the Admin > API Key sub-section.
# ---------------------------------------------------------------------------


def test_settings_page_includes_admin_api_key_section(
    db: None,
    app: TestClient,
) -> None:
    """The Settings page renders the Admin > API Key sub-section inline."""
    _login(app)

    response = app.get("/settings")

    assert response.status_code == 200
    assert b'id="admin-api-key"' in response.content
    assert b"Houndarr API key" in response.content
    assert b"Not configured" in response.content
    assert b'hx-post="/settings/api-key/generate"' in response.content
    assert b"https://av1155.github.io/houndarr/docs/reference/api-keys" in response.content


def test_settings_page_reflects_configured_api_key(
    db: None,
    app: TestClient,
) -> None:
    """After generation, the Settings page reflects the configured state."""
    _login(app)
    plaintext_key = _generate_key(app)

    response = app.get("/settings")

    assert response.status_code == 200
    assert b'id="admin-api-key"' in response.content
    assert b"Configured" in response.content
    assert b"Regenerate key" in response.content
    assert b"Revoke" in response.content
    # The reload must not leak the plaintext back into the page.
    assert plaintext_key.encode("ascii") not in response.content


# ---------------------------------------------------------------------------
# POST /settings/api-key/generate
# ---------------------------------------------------------------------------


def test_api_key_generate_creates_hash_and_shows_plaintext_once(
    db: None,
    app: TestClient,
) -> None:
    """Generate persists a hash and returns the plaintext one time."""
    _login(app)

    response = app.post(
        "/settings/api-key/generate",
        headers=csrf_headers(app),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    plaintext_key = _extract_plaintext_key(response.content)
    assert b'id="admin-api-key"' in response.content
    assert b'data-copy-api-key="true"' in response.content
    assert b'id="api-key-reveal-modal"' in response.content
    stored = asyncio.run(widget_api_key.get())
    assert stored is not None
    assert stored.hash == hash_api_key(plaintext_key)


def test_api_key_generate_hx_response_triggers_reveal(db: None, app: TestClient) -> None:
    """The HX response sets HX-Trigger-After-Swap so the modal opens after swap."""
    _login(app)

    response = app.post(
        "/settings/api-key/generate",
        headers={**csrf_headers(app), "HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["HX-Trigger-After-Swap"] == "houndarr-show-api-key"
    assert b"<html" not in response.content
    assert b'id="admin-api-key"' in response.content
    assert b'id="api-key-reveal-modal"' in response.content
    plaintext_key = _extract_plaintext_key(response.content)
    stored = asyncio.run(widget_api_key.get())
    assert stored is not None
    assert stored.hash == hash_api_key(plaintext_key)


def test_api_key_regenerate_replaces_hash_and_invalidates_old_key(
    db: None,
    app: TestClient,
) -> None:
    """Regenerate rotates the stored hash and invalidates the previous key."""
    _login(app)
    first_key = _generate_key(app)
    first_stored = asyncio.run(widget_api_key.get())
    assert first_stored is not None

    second_key = _generate_key(app)
    second_stored = asyncio.run(widget_api_key.get())

    assert second_key != first_key
    assert second_stored is not None
    assert second_stored.hash != first_stored.hash
    assert second_stored.hash == hash_api_key(second_key)
    assert second_stored.last_used_at is None
    assert app.get("/api/v1/widget", headers={"X-Api-Key": first_key}).status_code == 401
    assert app.get("/api/v1/widget", headers={"X-Api-Key": second_key}).status_code == 200


# ---------------------------------------------------------------------------
# POST /settings/api-key/revoke
# ---------------------------------------------------------------------------


def test_api_key_revoke_clears_row_and_invalidates_key(
    db: None,
    app: TestClient,
) -> None:
    """Revoke deletes the stored row and immediately invalidates the key."""
    _login(app)
    plaintext_key = _generate_key(app)
    assert app.get("/api/v1/widget", headers={"X-Api-Key": plaintext_key}).status_code == 200

    response = app.post(
        "/settings/api-key/revoke",
        headers=csrf_headers(app),
    )

    assert response.status_code == 200
    assert b'id="admin-api-key"' in response.content
    assert b"Not configured" in response.content
    assert asyncio.run(widget_api_key.get()) is None
    revoked_response = app.get("/api/v1/widget", headers={"X-Api-Key": plaintext_key})
    assert revoked_response.status_code == 401
    assert revoked_response.headers["WWW-Authenticate"] == "ApiKey"


def test_api_key_revoke_hx_response_returns_fragment(db: None, app: TestClient) -> None:
    """Revoke HX response returns the section partial, no plaintext anywhere."""
    _login(app)
    plaintext_key = _generate_key(app)

    response = app.post(
        "/settings/api-key/revoke",
        headers={**csrf_headers(app), "HX-Request": "true"},
    )

    assert response.status_code == 200
    assert b"<html" not in response.content
    assert b'id="admin-api-key"' in response.content
    assert b"Not configured" in response.content
    assert plaintext_key.encode("ascii") not in response.content
    assert asyncio.run(widget_api_key.get()) is None


# ---------------------------------------------------------------------------
# Last-used timestamp surfaces in the section after a successful widget call.
# ---------------------------------------------------------------------------


def test_api_key_last_used_timestamp_is_shown(db: None, app: TestClient) -> None:
    """A successful /api/v1/widget hit updates last_used_at and the section."""
    _login(app)
    plaintext_key = _generate_key(app)

    widget_response = app.get("/api/v1/widget", headers={"X-Api-Key": plaintext_key})
    assert widget_response.status_code == 200
    stored = asyncio.run(widget_api_key.get())
    assert stored is not None
    assert stored.last_used_at is not None

    response = app.get("/settings")
    assert response.status_code == 200
    assert stored.last_used_at.encode("ascii") in response.content

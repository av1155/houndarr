"""Shared pytest fixtures for Houndarr tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from houndarr.auth import CSRF_COOKIE_NAME
from houndarr.config import AppSettings, bootstrap_settings
from houndarr.database import init_db, set_db_path

# ---------------------------------------------------------------------------
# Build-artefact fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _ensure_css_bundle_for_tests() -> Generator[None, None, None]:
    """Stub `app.built.css` so the lifespan preflight passes in tests.

    The real bundle is produced by `pnpm run build-css` (run by the
    Docker `css-build` stage) and is gitignored.  CI runs pytest
    against the checked-out source without the build step, so the
    file is absent.  The lifespan preflight added with #582 refuses
    to start without it, which would otherwise fail every TestClient
    fixture in the suite.

    Tests do not exercise CSS contents, only header behaviour and
    routing, so a small stub satisfies the contract.  Local dev
    runs that already produced the real bundle keep theirs.
    """
    from houndarr import app as app_module

    css_bundle = Path(app_module.__file__).parent / "static" / "css" / "app.built.css"
    created = False
    if not css_bundle.is_file():
        css_bundle.write_text("/* pytest stub */\n", encoding="utf-8")
        created = True
    try:
        yield
    finally:
        if created and css_bundle.is_file():
            css_bundle.unlink()


# ---------------------------------------------------------------------------
# Global speed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _zero_inter_search_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero the inter-search delay constant so tests run at full speed.

    The real delay exists to spread downstream indexer fan-out; tests do not
    hit live indexers, so 3-second waits between dispatched items would make
    the suite unusably slow.
    """
    import houndarr.engine.search_loop as _sl

    monkeypatch.setattr(_sl, "_INTER_SEARCH_DELAY_SECONDS", 0.0)


@pytest.fixture()
def tmp_data_dir() -> Generator[str, None, None]:
    """Provide a temporary data directory for each test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest_asyncio.fixture()
async def db(tmp_data_dir: str) -> AsyncGenerator[None, None]:
    """Initialize a fresh in-memory-style SQLite DB for each test.

    Also resets the auth-package caches so tests that request only
    ``db`` (not ``test_settings``) cannot see a ``_setup_complete`` /
    ``_serializer`` / ``_login_attempts`` state from a prior test.
    """
    from houndarr.auth import reset_auth_caches

    db_path = os.path.join(tmp_data_dir, "test.db")
    set_db_path(db_path)
    await init_db()
    reset_auth_caches()
    yield


@pytest.fixture()
def test_settings(tmp_data_dir: str) -> AppSettings:
    """Return AppSettings pointing at tmp data dir."""
    settings = bootstrap_settings(data_dir=tmp_data_dir)
    # Reset every auth-package cache so each test starts clean.  Covers
    # the session serializer (re-keyed on next ``_get_serializer()``),
    # the setup-complete flag, and the in-memory rate-limit buckets.
    from houndarr.auth import reset_auth_caches

    reset_auth_caches()
    return settings


@pytest.fixture()
def app(test_settings: AppSettings) -> Generator[TestClient, None, None]:
    """Create a TestClient for the FastAPI app."""
    from houndarr.app import create_app

    application = create_app()
    with TestClient(application, raise_server_exceptions=True) as client:
        yield client


@pytest_asyncio.fixture()
async def async_client(
    test_settings: AppSettings,
) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTPX client for testing async endpoints."""
    from houndarr.app import create_app

    application = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# CSRF helpers
# ---------------------------------------------------------------------------


def get_csrf_token(client: TestClient) -> str:
    """Extract the CSRF token from the client's cookie jar.

    After a successful login, the server sets a readable ``houndarr_csrf``
    cookie containing the per-session token.  Call this after logging in to
    obtain the token needed for mutating requests in tests.

    Args:
        client: An authenticated :class:`TestClient` with a live session.

    Returns:
        The CSRF token string (empty string if not found).
    """
    return client.cookies.get(CSRF_COOKIE_NAME) or ""


def csrf_headers(client: TestClient) -> dict[str, str]:
    """Return an ``X-CSRF-Token`` header dict for use in mutating requests.

    Convenience wrapper around :func:`get_csrf_token` for passing to the
    ``headers`` parameter of ``client.post`` / ``client.delete`` calls.

    Args:
        client: An authenticated :class:`TestClient` with a live session.

    Returns:
        Dict with the ``X-CSRF-Token`` header set to the current token.
    """
    return {"X-CSRF-Token": get_csrf_token(client)}

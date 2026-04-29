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


@pytest.fixture(autouse=True)
def _ensure_css_bundle_for_tests() -> None:
    """Stub `app.built.css` so the lifespan preflight passes in tests.

    The real bundle is produced by `pnpm run build-css` (run by the
    Docker `css-build` stage) and is gitignored.  CI runs pytest
    against the checked-out source without the build step, so the
    file is absent.  The lifespan preflight added with #582 refuses
    to start without it, which would otherwise fail every TestClient
    fixture in the suite.

    Function-scoped on purpose: under ``pytest-xdist`` the workers each
    own their own session, and a session-scope teardown that deleted
    the stub raced with sibling workers still running tests, sporadically
    breaking unrelated TestClient fixtures.  No teardown here: the file
    is gitignored, the next CI run starts clean, and local dev runs that
    already produced the real bundle still see theirs.
    """
    from houndarr import app as app_module

    css_bundle = Path(app_module.__file__).parent / "static" / "css" / "app.built.css"
    if not css_bundle.is_file():
        css_bundle.write_text("/* pytest stub */\n", encoding="utf-8")


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


@pytest.fixture(autouse=True)
def _disable_dashboard_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the dashboard aggregate cache off in the default fixture.

    The legacy tests in ``tests/test_routes/test_status.py`` and
    sibling files seed ``search_log`` directly via SQL between
    ``/api/status`` calls; the production cache would serve the
    pre-seed snapshot back to the second call and break their
    assertions.  Setting ``ttl=0`` makes
    :func:`build_aggregate_cache` return ``None`` so every request
    hits the database, matching the v1.10.x behaviour the tests were
    written against.

    Cache-specific tests opt back in by re-monkeypatching
    ``DASHBOARD_CACHE_TTL_SECONDS`` to a non-zero value before
    constructing their own app, or by calling
    :func:`build_aggregate_cache(ttl_seconds=20)` directly.
    """
    import houndarr.services.metrics as _metrics

    monkeypatch.setattr(_metrics, "DASHBOARD_CACHE_TTL_SECONDS", 0)


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

    Yields, then closes any aiosqlite connection pools created against
    the temp file so the per-test ``tmp_data_dir`` cleanup can release
    the SQLite handle.  Without the close-on-exit step the pool would
    keep one or more aiosqlite reader threads alive until process exit
    and the OS would refuse to delete the temp directory on Windows
    runners.
    """
    from houndarr.auth import reset_auth_caches
    from houndarr.database import close_all_pools

    db_path = os.path.join(tmp_data_dir, "test.db")
    set_db_path(db_path)
    await init_db()
    reset_auth_caches()
    try:
        yield
    finally:
        await close_all_pools()


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

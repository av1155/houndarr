"""Shared pytest fixtures for Houndarr tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from houndarr import config as _cfg
from houndarr.auth import CSRF_COOKIE_NAME
from houndarr.config import AppSettings
from houndarr.database import init_db, set_db_path

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
    """Initialize a fresh in-memory-style SQLite DB for each test."""
    db_path = os.path.join(tmp_data_dir, "test.db")
    set_db_path(db_path)
    await init_db()
    yield


@pytest.fixture()
def test_settings(tmp_data_dir: str) -> AppSettings:
    """Return AppSettings pointing at tmp data dir."""
    settings = AppSettings(data_dir=tmp_data_dir)
    _cfg._runtime_settings = settings  # noqa: SLF001
    # Reset auth module singletons so each test starts with a clean state.
    # - _serializer: re-initialized with the new test DB's session_secret.
    # - _login_attempts: cleared so rate-limit counters don't bleed between tests.
    import houndarr.auth as _auth

    _auth._serializer = None  # noqa: SLF001
    _auth._login_attempts.clear()  # noqa: SLF001
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

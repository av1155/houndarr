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
from houndarr.config import AppSettings
from houndarr.database import init_db, set_db_path


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

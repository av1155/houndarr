"""Tests for GET /api/v1/widget."""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from houndarr.auth import _widget_key_attempts
from houndarr.auth.houndarr_api_key import generate_api_key, hash_api_key
from houndarr.config import AppSettings
from houndarr.database import get_db
from houndarr.repositories import widget_api_key


async def _store_widget_token() -> str:
    token = generate_api_key()
    await widget_api_key.set(hash_api_key(token))
    return token


async def _seed_widget_status_rows() -> None:
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO instances (
                id, name, type, url, encrypted_api_key, enabled,
                monitored_total, unreleased_count
            )
            VALUES (1, 'Widget Sonarr', 'sonarr', 'http://sonarr:8989', '', 1, 10, 1)
            """
        )
        await conn.executemany(
            """
            INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)
            VALUES (1, ?, 'episode', ?, datetime('now', '-1 day'))
            """,
            [(101, "missing"), (102, "cutoff"), (103, "upgrade")],
        )
        await conn.executemany(
            "INSERT INTO search_log (instance_id, action, timestamp) "
            "VALUES (1, ?, datetime('now', ?))",
            [("searched", "-1 day"), ("searched", "-8 days"), ("skipped", "-1 day")],
        )
        await conn.commit()


@pytest.fixture()
def proxy_widget_app(db: None, test_settings: AppSettings) -> Generator[TestClient]:
    test_settings.auth_mode = "proxy"
    test_settings.auth_proxy_header = "Remote-User"
    test_settings.trusted_proxies = "127.0.0.1"
    test_settings._trusted_proxy_cache = None

    from houndarr.app import create_app

    application = create_app()
    with TestClient(application, raise_server_exceptions=True) as client:
        yield client


def test_widget_returns_locked_envelope_for_valid_key(db: None, app: TestClient) -> None:
    asyncio.run(_seed_widget_status_rows())
    token = asyncio.run(_store_widget_token())

    resp = app.get("/api/v1/widget", headers={"X-Api-Key": token})

    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == 1
    assert isinstance(body["generated_at"], str)
    assert body["totals"] == {
        "tracked": 11,
        "eligible": 7,
        "gated": 2,
        "unreleased": 1,
        "searches_7d": 1,
    }
    stored = asyncio.run(widget_api_key.get())
    assert stored is not None
    assert stored.last_used_at is not None


def test_widget_without_configured_key_returns_401(db: None, app: TestClient) -> None:
    resp = app.get("/api/v1/widget", headers={"X-Api-Key": "not-the-token"})

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_widget_missing_key_returns_401(db: None, app: TestClient) -> None:
    asyncio.run(_store_widget_token())

    resp = app.get("/api/v1/widget")

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_widget_invalid_key_returns_401(db: None, app: TestClient) -> None:
    asyncio.run(_store_widget_token())

    resp = app.get("/api/v1/widget", headers={"X-Api-Key": "not-the-token"})

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_widget_valid_key_clears_prior_failed_attempts(db: None, app: TestClient) -> None:
    token = asyncio.run(_store_widget_token())

    for _ in range(5):
        resp = app.get("/api/v1/widget", headers={"X-Api-Key": "not-the-token"})
        assert resp.status_code == 401

    resp = app.get("/api/v1/widget", headers={"X-Api-Key": token})
    assert resp.status_code == 200
    assert _widget_key_attempts == {}


def test_widget_rate_limits_failed_key_attempts(db: None, app: TestClient) -> None:
    asyncio.run(_store_widget_token())

    for _ in range(5):
        resp = app.get("/api/v1/widget", headers={"X-Api-Key": "not-the-token"})
        assert resp.status_code == 401

    resp = app.get("/api/v1/widget", headers={"X-Api-Key": "not-the-token"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"


def test_widget_valid_key_works_in_proxy_auth_mode(proxy_widget_app: TestClient) -> None:
    token = asyncio.run(_store_widget_token())

    resp = proxy_widget_app.get("/api/v1/widget", headers={"X-Api-Key": token})

    assert resp.status_code == 200
    assert resp.json()["totals"] == {
        "tracked": 0,
        "eligible": 0,
        "gated": 0,
        "unreleased": 0,
        "searches_7d": 0,
    }


def test_widget_ignores_proxy_header_without_api_key(proxy_widget_app: TestClient) -> None:
    asyncio.run(_store_widget_token())

    resp = proxy_widget_app.get("/api/v1/widget", headers={"Remote-User": "alice"})

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_existing_route_auth_buckets_are_unchanged(db: None, app: TestClient) -> None:
    assert app.get("/api/health").status_code == 200
    assert app.get("/api/status", follow_redirects=False).status_code == 302
    assert app.get("/settings", follow_redirects=False).status_code == 302

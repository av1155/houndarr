"""Routes keep working when a stored API key does not decrypt (#934).

The instances below are encrypted under a master key the app does not
hold, as after a restore that left ``houndarr.masterkey`` behind.
"""

from __future__ import annotations

import asyncio
import html
import sqlite3
from collections.abc import Generator
from contextlib import closing
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from houndarr.app import create_app
from houndarr.clients._wire_models import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.config import AppSettings
from houndarr.crypto import decrypt, encrypt
from houndarr.database import close_all_pools, get_db, init_db, set_db_path
from houndarr.engine.supervisor import Supervisor
from tests.conftest import csrf_headers

OTHER_KEY: bytes = Fernet.generate_key()
LOST, LOST_PAUSED = 1, 2
KEY_ERROR = "API key can't be decrypted with the current master key; enter it again in Settings"
EDIT_ALERT = "The stored API key can't be decrypted. Enter it again."
_FORM = {
    "name": "Lost Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "sonarr_search_mode": "episode",
    "connection_verified": "true",
}


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the API key every connection check sends."""
    sent: list[str] = []

    async def _always_ok(self: ArrClient) -> SystemStatus | None:
        sent.append(self._client.headers["X-Api-Key"])
        return SystemStatus(app_name="Sonarr", version="4.0.0")

    monkeypatch.setattr(ArrClient, "ping", _always_ok)
    return sent


async def _seed(db_path: Path) -> None:
    set_db_path(str(db_path))
    await init_db()
    try:
        async with get_db() as conn:
            await conn.executemany(
                "INSERT INTO instances (id, name, type, url, encrypted_api_key, enabled)"
                " VALUES (?, ?, 'sonarr', 'http://sonarr:8989', ?, ?)",
                [
                    (LOST, "Lost Sonarr", encrypt("old", OTHER_KEY), 1),
                    (LOST_PAUSED, "Paused Sonarr", encrypt("old", OTHER_KEY), 0),
                ],
            )
            await conn.commit()
    finally:
        await close_all_pools()


@pytest.fixture()
def client(test_settings: AppSettings) -> Generator[TestClient]:
    asyncio.run(_seed(test_settings.db_path))
    with TestClient(create_app(), raise_server_exceptions=True) as test_client:
        test_client.post(
            "/setup",
            data={
                "username": "admin",
                "password": "ValidPass1!",
                "password_confirm": "ValidPass1!",
            },
        )
        test_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})
        yield test_client


def _row_html(page: str, instance_id: int) -> str:
    start = page.index(f'id="instance-row-{instance_id}"')
    return page[start : page.index("</tr>", start)]


def _stored_key(db_path: Path, instance_id: int) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT encrypted_api_key FROM instances WHERE id = ?", (instance_id,)
        ).fetchone()
    return str(row[0])


def _log_rows(db_path: Path, instance_id: int) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT action, message FROM search_log WHERE instance_id = ? ORDER BY id",
            (instance_id,),
        ).fetchall()


def _supervisor(client: TestClient) -> Supervisor:
    supervisor = client.app.state.supervisor  # type: ignore[attr-defined]
    assert isinstance(supervisor, Supervisor)
    return supervisor


def test_settings_page_shows_the_error_and_the_disabled_instance(client: TestClient) -> None:
    resp = client.get("/settings")

    assert resp.status_code == 200
    assert "status-pill--error" in _row_html(resp.text, LOST)
    assert "status-pill--disabled" in _row_html(resp.text, LOST_PAUSED)


def test_dashboard_and_logs_show_the_key_error(client: TestClient) -> None:
    status = client.get("/api/status")
    logs = client.get("/logs")

    assert status.status_code == 200
    [lost] = [inst for inst in status.json()["instances"] if inst["id"] == LOST]
    assert lost["active_error"]["message"] == KEY_ERROR
    assert logs.status_code == 200
    assert KEY_ERROR in html.unescape(logs.text)


@pytest.mark.parametrize("instance_id", [LOST, LOST_PAUSED])
def test_edit_asks_for_the_key_again(client: TestClient, instance_id: int) -> None:
    resp = client.get(f"/settings/instances/{instance_id}/edit")

    assert resp.status_code == 200
    assert EDIT_ALERT in html.unescape(resp.text)
    assert 'value="__UNCHANGED__"' not in resp.text
    assert 'placeholder="Enter the API key again"' in resp.text


def test_test_connection_refuses_the_stored_key_it_cannot_decrypt(
    client: TestClient, _mock_connection_ping: list[str]
) -> None:
    resp = client.post(
        "/settings/instances/test-connection",
        data={
            "type": "sonarr",
            "url": "http://sonarr:8989",
            "api_key": "__UNCHANGED__",
            "instance_id": str(LOST),
        },
        headers=csrf_headers(client),
    )

    assert resp.status_code == 422
    assert EDIT_ALERT in html.unescape(resp.text)
    assert _mock_connection_ping == []


def test_save_refuses_the_stored_key_it_cannot_decrypt(
    client: TestClient, test_settings: AppSettings, _mock_connection_ping: list[str]
) -> None:
    before = _stored_key(test_settings.db_path, LOST)

    resp = client.post(
        f"/settings/instances/{LOST}",
        data={**_FORM, "api_key": "__UNCHANGED__"},
        headers=csrf_headers(client),
    )

    assert resp.status_code == 422
    assert EDIT_ALERT in html.unescape(resp.text)
    assert resp.headers.get("HX-Trigger") == "houndarr-connection-test-failure"
    assert _stored_key(test_settings.db_path, LOST) == before
    assert _mock_connection_ping == []


@pytest.mark.parametrize("api_key", ["", "   "])
def test_a_blank_api_key_is_refused_everywhere(
    client: TestClient, _mock_connection_ping: list[str], api_key: str
) -> None:
    # As the dialog sends it: htmx gets an empty 422 for a field FastAPI calls missing.
    headers = {**csrf_headers(client), "HX-Request": "true"}
    probe = client.post(
        "/settings/instances/test-connection",
        data={"type": "sonarr", "url": "http://sonarr:8989", "api_key": api_key},
        headers=headers,
    )
    create = client.post(
        "/settings/instances",
        data={**_FORM, "name": "New", "api_key": api_key},
        headers=headers,
    )
    update = client.post(
        f"/settings/instances/{LOST}", data={**_FORM, "api_key": api_key}, headers=headers
    )

    for resp in (probe, create, update):
        assert resp.status_code == 422
        assert "Provide an API key." in resp.text
    assert _mock_connection_ping == []


def test_test_connection_with_an_empty_url_says_so(client: TestClient) -> None:
    resp = client.post(
        "/settings/instances/test-connection",
        data={"type": "sonarr", "url": "", "api_key": "k"},
        headers={**csrf_headers(client), "HX-Request": "true"},
    )

    assert resp.status_code == 422
    assert "Instance URL is required." in resp.text


def test_entering_the_key_again_restarts_the_instance(
    client: TestClient, test_settings: AppSettings
) -> None:
    resp = client.post(
        f"/settings/instances/{LOST}",
        data={**_FORM, "api_key": "new-key"},
        headers=csrf_headers(client),
    )

    assert resp.status_code == 200
    assert "status-pill--active" in _row_html(resp.text, LOST)
    master_key = client.app.state.master_key  # type: ignore[attr-defined]
    assert decrypt(_stored_key(test_settings.db_path, LOST), master_key) == "new-key"
    assert LOST in _supervisor(client)._tasks
    assert _log_rows(test_settings.db_path, LOST) == [
        ("error", KEY_ERROR),
        ("info", "'Lost Sonarr' API key can be decrypted again"),
    ]
    edit = client.get(f"/settings/instances/{LOST}/edit")
    assert 'value="__UNCHANGED__"' in edit.text
    assert EDIT_ALERT not in html.unescape(edit.text)


def test_enabling_an_instance_with_an_unreadable_key_shows_the_error(
    client: TestClient, test_settings: AppSettings
) -> None:
    resp = client.post(
        f"/settings/instances/{LOST_PAUSED}/toggle-enabled", headers=csrf_headers(client)
    )

    assert resp.status_code == 200
    assert "status-pill--error" in _row_html(resp.text, LOST_PAUSED)
    assert LOST_PAUSED not in _supervisor(client)._tasks
    assert _log_rows(test_settings.db_path, LOST_PAUSED) == [("error", KEY_ERROR)]


def test_run_now_refuses_an_unreadable_key(client: TestClient) -> None:
    resp = client.post(f"/api/instances/{LOST}/run-now", headers=csrf_headers(client))

    assert resp.status_code == 409
    assert resp.json()["detail"] == KEY_ERROR


def test_reset_instance_settings_still_works(client: TestClient) -> None:
    resp = client.post("/settings/admin/reset-instances", headers=csrf_headers(client))

    assert resp.status_code == 200
    assert "Policy settings reset" in resp.text

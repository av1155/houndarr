"""Pin the admin bulk-destructive endpoints' response contract.

Locks the response tone, status, and phrasing on
``/settings/admin/reset-instances``,
``/settings/admin/clear-logs``, and
``/settings/admin/factory-reset`` so later edits to the routes or
the underlying service cannot silently change what the user sees.

These are end-to-end route tests driven through ``TestClient``.
The existing ``tests/test_routes/test_admin.py`` has thorough
coverage of the happy paths; this file adds the narrow pinning
subset that must stay byte-stable.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from houndarr.clients._wire_models import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.database import get_db
from tests.conftest import csrf_headers

pytestmark = pytest.mark.pinning


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance creation below triggers ArrClient.ping via the connection check."""

    async def _always_ok(self: ArrClient) -> SystemStatus | None:
        name = type(self).__name__.replace("Client", "")
        return SystemStatus(app_name=name, version="4.0.0")

    monkeypatch.setattr(ArrClient, "ping", _always_ok)


def _login(client: TestClient) -> None:
    client.post(
        "/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


@pytest_asyncio.fixture()
async def logged_in_client(app: TestClient) -> AsyncGenerator[TestClient, None]:
    _login(app)
    yield app


# Reset-instances endpoint


class TestResetInstances:
    def test_empty_install_returns_no_instances_message(self, logged_in_client: TestClient) -> None:
        """With zero configured instances the response explains why nothing changed."""
        resp = logged_in_client.post(
            "/settings/admin/reset-instances",
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 200
        assert "No instances configured" in resp.text

    def test_without_csrf_is_rejected(self, logged_in_client: TestClient) -> None:
        """CSRF middleware rejects the mutating POST without the header."""
        resp = logged_in_client.post("/settings/admin/reset-instances")
        assert resp.status_code == 403


# Clear-logs endpoint


class TestClearLogs:
    def test_empty_log_table_returns_already_empty(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/admin/clear-logs",
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 200
        assert "already empty" in resp.text.lower()

    @pytest.mark.asyncio()
    async def test_non_empty_log_count_reflected_in_message(
        self,
        logged_in_client: TestClient,
    ) -> None:
        """After seeding three search_log rows the response names the count."""
        async with get_db() as db:
            await db.executemany(
                "INSERT INTO search_log (instance_id, item_id, item_type, action) "
                "VALUES (NULL, ?, 'movie', 'info')",
                [(1,), (2,), (3,)],
            )
            await db.commit()
        resp = logged_in_client.post(
            "/settings/admin/clear-logs",
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 200
        assert "3 log rows" in resp.text or "Cleared 3" in resp.text

    def test_without_csrf_is_rejected(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post("/settings/admin/clear-logs")
        assert resp.status_code == 403


# Factory-reset endpoint (builtin mode)


class TestFactoryResetBuiltin:
    def test_missing_confirm_phrase_returns_422(self, logged_in_client: TestClient) -> None:
        """Without 'RESET' typed, the endpoint refuses and returns 422."""
        resp = logged_in_client.post(
            "/settings/admin/factory-reset",
            data={"current_password": "ValidPass1!"},
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "RESET" in resp.text

    def test_wrong_password_returns_422(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/admin/factory-reset",
            data={"confirm_phrase": "RESET", "current_password": "WrongPass1!"},
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "Current password is incorrect" in resp.text

    def test_without_csrf_is_rejected(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/admin/factory-reset",
            data={"confirm_phrase": "RESET", "current_password": "ValidPass1!"},
        )
        assert resp.status_code == 403

    def test_happy_path_returns_hx_redirect_to_setup(
        self,
        logged_in_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Correct phrase + password: response carries HX-Redirect and clears session."""
        from houndarr.routes import admin as admin_mod

        # Stub the destructive call so the test DB survives.
        monkeypatch.setattr(admin_mod, "factory_reset", AsyncMock(return_value=None))

        resp = logged_in_client.post(
            "/settings/admin/factory-reset",
            data={"confirm_phrase": "RESET", "current_password": "ValidPass1!"},
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect") == "/setup"

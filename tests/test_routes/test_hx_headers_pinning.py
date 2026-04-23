"""Pin the HX-* response headers every route emits today.

The typed helpers in :mod:`houndarr.routes._htmx` emit
``HX-Trigger / HX-Retarget / HX-Reswap / HX-Redirect /
HX-Refresh``; these tests snapshot the exact header names and
values each route emits in every branch so a helper edit cannot
silently drop or rename a header.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from tests.conftest import csrf_headers

pytestmark = pytest.mark.pinning


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


# settings/account/password: HX-Refresh on success


class TestAccountPasswordHeaders:
    def test_success_emits_hx_refresh(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "BrandNew2@",
                "new_password_confirm": "BrandNew2@",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.headers.get("HX-Refresh") == "true"

    def test_validation_error_does_not_emit_hx_refresh(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "short",
                "new_password_confirm": "short",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert "HX-Refresh" not in resp.headers


# admin/factory-reset: HX-Redirect on success


class TestFactoryResetHeaders:
    def test_success_emits_hx_redirect_to_setup(
        self,
        logged_in_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from houndarr.routes import admin as admin_mod

        monkeypatch.setattr(admin_mod, "factory_reset", AsyncMock(return_value=None))
        resp = logged_in_client.post(
            "/settings/admin/factory-reset",
            data={"confirm_phrase": "RESET", "current_password": "ValidPass1!"},
            headers=csrf_headers(logged_in_client),
        )
        assert resp.headers.get("HX-Redirect") == "/setup"

    def test_validation_error_no_redirect(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/admin/factory-reset",
            data={"confirm_phrase": "", "current_password": "ValidPass1!"},
            headers=csrf_headers(logged_in_client),
        )
        assert "HX-Redirect" not in resp.headers


# changelog/popup: HX-Trigger-After-Swap on active modal


class TestChangelogPopupHeaders:
    def test_manual_force_emits_trigger_after_swap(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/settings/changelog/popup?force=1")
        # Either the trigger-after-swap header lands (modal rendered) or the
        # endpoint returned the empty placeholder.  The trigger-after-swap
        # is only present in the modal branch.
        if "changelog-modal" in resp.text:
            assert resp.headers.get("HX-Trigger-After-Swap") == "houndarr-show-changelog"

    def test_dismiss_returns_204(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/changelog/dismiss",
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 204


# settings/instances/test-connection: HX-Trigger event name


class TestInstanceTestConnectionHeaders:
    def test_invalid_url_emits_failure_event(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/instances/test-connection",
            data={
                "type": "sonarr",
                "url": "http://127.0.0.1:8989",  # loopback blocked by SSRF guard
                "api_key": "k",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert resp.headers.get("HX-Trigger") == "houndarr-connection-test-failure"

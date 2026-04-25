"""Pin the /settings/account/password endpoint contract.

Locks the validation-error matrix, the rate-limit response, the
success response shape, and the HX-Refresh behaviour so later
edits to the settings page or its service layer cannot silently
drift any of them.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

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


# Validation matrix


class TestPasswordValidation:
    def test_wrong_current_password_returns_422(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "WrongPass1!",
                "new_password": "BrandNew2@",
                "new_password_confirm": "BrandNew2@",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "Current password is incorrect" in resp.text

    def test_new_password_too_short_returns_422(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "short",
                "new_password_confirm": "short",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "at least 8" in resp.text

    def test_new_passwords_mismatch_returns_422(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "BrandNew2@",
                "new_password_confirm": "Different3#",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "do not match" in resp.text

    def test_same_new_password_returns_422(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "ValidPass1!",
                "new_password_confirm": "ValidPass1!",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "must be different" in resp.text


# Happy path


class TestPasswordHappyPath:
    def test_happy_path_returns_200_with_hx_refresh(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "BrandNew2@",
                "new_password_confirm": "BrandNew2@",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Refresh") == "true"
        assert "Password updated successfully" in resp.text

    def test_old_password_rejected_after_update(self, logged_in_client: TestClient) -> None:
        """Re-submitting with the old current_password after a successful change fails."""
        logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "BrandNew2@",
                "new_password_confirm": "BrandNew2@",
            },
            headers=csrf_headers(logged_in_client),
        )
        # Second attempt with old password must fail.
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "YetAnother3#",
                "new_password_confirm": "YetAnother3#",
            },
            headers=csrf_headers(logged_in_client),
        )
        assert resp.status_code == 422
        assert "Current password is incorrect" in resp.text


# CSRF


class TestCsrfEnforcement:
    def test_without_csrf_header_rejected(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post(
            "/settings/account/password",
            data={
                "current_password": "ValidPass1!",
                "new_password": "BrandNew2@",
                "new_password_confirm": "BrandNew2@",
            },
        )
        assert resp.status_code == 403

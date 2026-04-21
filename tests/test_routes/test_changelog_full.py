"""Tests for GET /settings/changelog/full (full CHANGELOG render)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import houndarr.services.changelog as _cl


@pytest.fixture(autouse=True)
def _reset_changelog_cache() -> None:
    """Bust any cache populated by earlier tests that monkeypatched CHANGELOG_PATH."""
    _cl._reset_changelog_cache()  # noqa: SLF001


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


def test_changelog_full_requires_auth(app: TestClient) -> None:
    resp = app.get("/settings/changelog/full", follow_redirects=False)
    assert resp.status_code == 302


def test_changelog_full_renders_full_page(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/changelog/full")
    assert resp.status_code == 200
    # Non-HX request returns a full HTML page extending base.html.
    assert b"<html" in resp.content
    assert b"Changelog" in resp.content


def test_changelog_full_hx_returns_partial(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/changelog/full", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    # HX request returns only the content partial (no <html> wrapper).
    assert b"<html" not in resp.content
    assert b'data-page-key="changelog-full"' in resp.content


def test_changelog_full_renders_release_history(app: TestClient) -> None:
    _login(app)
    resp = app.get("/settings/changelog/full")
    assert resp.status_code == 200
    # Bundled CHANGELOG.md has real release entries; the page should name
    # at least one version-like heading (no empty-state copy).
    assert b"data-changelog-release" in resp.content

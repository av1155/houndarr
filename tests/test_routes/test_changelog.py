"""Tests for the changelog modal routes and setup seeding."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from houndarr import __version__
from houndarr.database import get_setting, set_setting
from houndarr.services import changelog as cl
from tests.conftest import csrf_headers

_SAMPLE_CHANGELOG = f"""# Changelog

## [{__version__}] - 2026-04-16

### Added

- New feature with `backticks` and [a link](https://example.com). (#408)

### Fixed

- A bug. (#123)

---

## [1.7.0] - 2026-04-04

### Added

- Older feature. (#338)

---

## [1.6.0] - 2026-03-21

### Changed

- Earlier change. (#272)

---
"""


@pytest.fixture()
def sample_changelog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the service module at a fixture CHANGELOG.md with known content."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_SAMPLE_CHANGELOG, encoding="utf-8")
    monkeypatch.setattr(cl, "CHANGELOG_PATH", path)
    cl._reset_changelog_cache()
    return path


def _login(client: TestClient) -> None:
    """Complete setup + login so subsequent requests are authenticated."""
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


def test_popup_requires_auth(app: TestClient) -> None:
    resp = app.get("/settings/changelog/popup", follow_redirects=False)
    assert resp.status_code == 302


def test_dismiss_requires_auth(app: TestClient) -> None:
    resp = app.post("/settings/changelog/dismiss", follow_redirects=False)
    assert resp.status_code == 302


def test_disable_requires_auth(app: TestClient) -> None:
    resp = app.post("/settings/changelog/disable", follow_redirects=False)
    assert resp.status_code == 302


def test_preferences_requires_auth(app: TestClient) -> None:
    resp = app.post("/settings/changelog/preferences", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# CSRF gate on POST routes
# ---------------------------------------------------------------------------


def test_dismiss_rejects_missing_csrf(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    resp = app.post("/settings/changelog/dismiss")  # no CSRF header
    assert resp.status_code == 403


def test_disable_rejects_missing_csrf(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    resp = app.post("/settings/changelog/disable")
    assert resp.status_code == 403


def test_preferences_rejects_missing_csrf(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    resp = app.post("/settings/changelog/preferences")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Setup seeding: fresh install writes last_seen_version = current
# ---------------------------------------------------------------------------


async def test_setup_seeds_changelog_last_seen_version(
    app: TestClient, sample_changelog: Path
) -> None:
    _login(app)
    stored = await get_setting("changelog_last_seen_version")
    assert stored == __version__


async def test_fresh_install_popup_returns_empty(app: TestClient, sample_changelog: Path) -> None:
    """Post-setup, running == last_seen, so auto-popup returns the empty placeholder."""
    _login(app)
    resp = app.get("/settings/changelog/popup")
    assert resp.status_code == 200
    assert "<dialog" not in resp.text
    assert 'id="changelog-slot"' in resp.text
    assert "HX-Trigger" not in resp.headers


# ---------------------------------------------------------------------------
# Auto-popup: upgrade scenario
# ---------------------------------------------------------------------------


async def test_upgrade_popup_renders_modal(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    # Simulate a user who was on 1.6.0 before upgrading to the current version.
    await set_setting("changelog_last_seen_version", "1.6.0")
    resp = app.get("/settings/changelog/popup")
    assert resp.status_code == 200
    assert 'id="changelog-modal"' in resp.text
    assert "<dialog" in resp.text
    assert resp.headers.get("HX-Trigger-After-Swap") == "houndarr-show-changelog"
    # Newest and one older release should both appear.
    assert f"v{__version__}" in resp.text
    assert "v1.7.0" in resp.text


async def test_upgrade_popup_renders_bullet_markdown(
    app: TestClient, sample_changelog: Path
) -> None:
    _login(app)
    await set_setting("changelog_last_seen_version", "1.6.0")
    resp = app.get("/settings/changelog/popup")
    # Inline code, link, and issue ref all render as HTML.
    assert '<code class="text-brand-300">backticks</code>' in resp.text
    assert 'href="https://example.com"' in resp.text
    assert 'href="https://github.com/av1155/houndarr/issues/408"' in resp.text


async def test_disabled_popup_returns_empty(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    await set_setting("changelog_last_seen_version", "1.6.0")
    await set_setting("changelog_popups_disabled", "1")
    resp = app.get("/settings/changelog/popup")
    assert resp.status_code == 200
    assert "<dialog" not in resp.text


# ---------------------------------------------------------------------------
# Force mode: manual re-open from Settings
# ---------------------------------------------------------------------------


async def test_force_popup_renders_current_version_only(
    app: TestClient, sample_changelog: Path
) -> None:
    _login(app)  # last_seen == current via setup seed
    resp = app.get("/settings/changelog/popup?force=1")
    assert resp.status_code == 200
    assert 'id="changelog-modal"' in resp.text
    assert "<dialog" in resp.text
    assert resp.headers.get("HX-Trigger-After-Swap") == "houndarr-show-changelog"
    # Only the current version renders in force mode.
    assert f"v{__version__}" in resp.text
    assert "v1.6.0" not in resp.text
    # "Don't show again" is hidden in manual mode.
    assert "Don't show again" not in resp.text


async def test_force_popup_does_not_mutate_persistence(
    app: TestClient, sample_changelog: Path
) -> None:
    _login(app)
    await set_setting("changelog_last_seen_version", "1.6.0")
    app.get("/settings/changelog/popup?force=1")
    # Last-seen unchanged.
    assert await get_setting("changelog_last_seen_version") == "1.6.0"


# ---------------------------------------------------------------------------
# Dismiss: writes last_seen = current, idempotent
# ---------------------------------------------------------------------------


async def test_dismiss_writes_current_version(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    await set_setting("changelog_last_seen_version", "1.6.0")
    resp = app.post("/settings/changelog/dismiss", headers=csrf_headers(app))
    assert resp.status_code == 204
    assert await get_setting("changelog_last_seen_version") == __version__


async def test_dismiss_is_idempotent(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    app.post("/settings/changelog/dismiss", headers=csrf_headers(app))
    resp = app.post("/settings/changelog/dismiss", headers=csrf_headers(app))
    assert resp.status_code == 204
    assert await get_setting("changelog_last_seen_version") == __version__


# ---------------------------------------------------------------------------
# Disable: writes both keys
# ---------------------------------------------------------------------------


async def test_disable_writes_both_keys(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    await set_setting("changelog_last_seen_version", "1.6.0")
    resp = app.post("/settings/changelog/disable", headers=csrf_headers(app))
    assert resp.status_code == 204
    assert await get_setting("changelog_last_seen_version") == __version__
    assert await get_setting("changelog_popups_disabled") == "1"


async def test_disable_then_popup_returns_empty(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    await set_setting("changelog_last_seen_version", "1.6.0")
    app.post("/settings/changelog/disable", headers=csrf_headers(app))
    resp = app.get("/settings/changelog/popup")
    assert "<dialog" not in resp.text


async def test_disabled_popup_silently_advances_last_seen(
    app: TestClient, sample_changelog: Path
) -> None:
    """While popups are disabled, every popup poll advances last_seen so
    that re-enabling later does not surface a backlog of releases the
    admin already chose to skip.
    """
    _login(app)
    # Simulate: admin was on 1.6.0, disabled popups (which writes 1.6.0
    # as last_seen via the Settings toggle path which leaves last_seen
    # untouched), then upgraded to current.
    await set_setting("changelog_last_seen_version", "1.6.0")
    await set_setting("changelog_popups_disabled", "1")
    resp = app.get("/settings/changelog/popup")
    assert resp.status_code == 200
    assert "<dialog" not in resp.text
    # Silent advance happened: last_seen now matches running.
    assert await get_setting("changelog_last_seen_version") == __version__


# ---------------------------------------------------------------------------
# Preferences toggle
# ---------------------------------------------------------------------------


async def test_preferences_enable_writes_zero(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    await set_setting("changelog_popups_disabled", "1")
    resp = app.post(
        "/settings/changelog/preferences",
        data={"enabled": "on"},
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert await get_setting("changelog_popups_disabled") == "0"
    # Returns the settings section partial with the checkbox checked.
    assert 'id="changelog-section"' in resp.text
    assert "checked" in resp.text


async def test_preferences_disable_writes_one(app: TestClient, sample_changelog: Path) -> None:
    _login(app)
    resp = app.post(
        "/settings/changelog/preferences",
        data={},  # unchecked = field absent
        headers=csrf_headers(app),
    )
    assert resp.status_code == 200
    assert await get_setting("changelog_popups_disabled") == "1"
    assert 'id="changelog-section"' in resp.text
    # The checkbox should NOT be checked when disabled.
    # We verify by locating the input element and checking the attribute.
    # This is a weak assertion; the stronger one is the value in the DB above.
    import re

    input_match = re.search(r'<input[^>]*name="enabled"[^>]*>', resp.text)
    assert input_match is not None
    assert "checked" not in input_match.group(0)


# ---------------------------------------------------------------------------
# Edge case: CHANGELOG.md missing at runtime
# ---------------------------------------------------------------------------


async def test_popup_when_changelog_missing(
    app: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(app)
    monkeypatch.setattr(cl, "CHANGELOG_PATH", tmp_path / "missing.md")
    cl._reset_changelog_cache()
    await set_setting("changelog_last_seen_version", "1.6.0")
    resp = app.get("/settings/changelog/popup")
    assert resp.status_code == 200
    # No parseable entries → empty placeholder, no 500.
    assert "<dialog" not in resp.text
    assert 'id="changelog-slot"' in resp.text


async def test_force_popup_when_changelog_missing(
    app: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(app)
    monkeypatch.setattr(cl, "CHANGELOG_PATH", tmp_path / "missing.md")
    cl._reset_changelog_cache()
    resp = app.get("/settings/changelog/popup?force=1")
    assert resp.status_code == 200
    assert "<dialog" not in resp.text


# ---------------------------------------------------------------------------
# Bullet renderer: scheme allowlist + balanced parens
# ---------------------------------------------------------------------------


def test_link_substitution_blocks_javascript_scheme() -> None:
    from houndarr.routes.changelog import _render_changelog_bullet

    rendered = str(_render_changelog_bullet("[click](javascript:alert(1))"))
    # No <a href="javascript:..."> emitted; original markdown text remains
    # visible (escape() already neutralised the < > < etc).
    assert 'href="javascript:' not in rendered
    assert "<a" not in rendered
    assert "[click]" in rendered


def test_link_substitution_blocks_data_scheme() -> None:
    from houndarr.routes.changelog import _render_changelog_bullet

    rendered = str(_render_changelog_bullet("[x](data:text/html,<script>alert(1)</script>)"))
    assert 'href="data:' not in rendered
    assert "<a" not in rendered


def test_link_substitution_allows_https() -> None:
    from houndarr.routes.changelog import _render_changelog_bullet

    rendered = str(_render_changelog_bullet("[ok](https://example.com/path)"))
    assert '<a href="https://example.com/path"' in rendered


def test_link_substitution_allows_mailto() -> None:
    from houndarr.routes.changelog import _render_changelog_bullet

    rendered = str(_render_changelog_bullet("[ping](mailto:dev@example.com)"))
    assert '<a href="mailto:dev@example.com"' in rendered


def test_link_substitution_allows_relative_and_fragment() -> None:
    from houndarr.routes.changelog import _render_changelog_bullet

    rendered = str(_render_changelog_bullet("[home](/) and [section](#anchor)"))
    assert '<a href="/"' in rendered
    assert '<a href="#anchor"' in rendered


def test_link_substitution_supports_balanced_parens_in_url() -> None:
    """URLs containing one level of nested parens (e.g. Wikipedia) render whole."""
    from houndarr.routes.changelog import _render_changelog_bullet

    raw = "see [Foo](https://en.wikipedia.org/wiki/Foo_(bar)) for details"
    rendered = str(_render_changelog_bullet(raw))
    assert '<a href="https://en.wikipedia.org/wiki/Foo_(bar)"' in rendered
    # No truncated link followed by literal "(bar))"
    assert "_bar)" not in rendered.replace('href="https://en.wikipedia.org/wiki/Foo_(bar)"', "")

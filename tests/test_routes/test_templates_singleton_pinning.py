"""Pinning tests for the centralised Jinja2Templates singleton.

Locks the shared-singleton contract: the five route packages that
render HTML share a single
:class:`fastapi.templating.Jinja2Templates` instance (built lazily
by :func:`houndarr.routes._templates.get_templates`) with both
custom filters (``timeago``, ``changelog_bullet``) wired in.
These tests fence:

- The singleton invariant: every route package returns the same
  underlying templates object.
- The filter invariant: both custom filters are registered on the
  shared environment.
- The reset hook: tests can null the module-level cache and the
  next call rebuilds.
"""

from __future__ import annotations

import pytest

from houndarr.routes import _templates as tpl
from houndarr.routes._templates import get_templates


@pytest.fixture(autouse=True)
def _reset_templates() -> None:
    """Clear the cached singleton before each test."""
    tpl._templates = None  # noqa: SLF001


@pytest.mark.pinning()
def test_get_templates_returns_singleton() -> None:
    """Successive calls return the same object."""
    first = get_templates()
    second = get_templates()
    assert first is second


@pytest.mark.pinning()
def test_get_templates_resolves_to_houndarr_templates_dir() -> None:
    """The shared templates directory is the package's templates folder."""
    templates = get_templates()
    loader = templates.env.loader
    # FileSystemLoader stores the search path in ``searchpath``.
    searchpath = loader.searchpath  # type: ignore[union-attr]
    assert any(path.endswith("houndarr/templates") for path in searchpath)


@pytest.mark.pinning()
def test_changelog_bullet_filter_is_registered() -> None:
    """``changelog_bullet`` is a registered Jinja filter on the shared env."""
    templates = get_templates()
    assert "changelog_bullet" in templates.env.filters


@pytest.mark.pinning()
def test_timeago_filter_is_registered() -> None:
    """``timeago`` is a registered Jinja filter on the shared env."""
    templates = get_templates()
    assert "timeago" in templates.env.filters


@pytest.mark.pinning()
def test_reset_hook_rebuilds_singleton() -> None:
    """Setting the module-level cache to None forces a fresh build."""
    first = get_templates()
    tpl._templates = None  # noqa: SLF001
    second = get_templates()
    assert first is not second


@pytest.mark.pinning()
def test_pages_module_uses_shared_templates() -> None:
    """routes.pages.get_templates is the same callable as the shared one."""
    from houndarr.routes import pages

    assert pages.get_templates is get_templates


@pytest.mark.pinning()
def test_admin_module_uses_shared_templates() -> None:
    """routes.admin.get_templates is the same callable as the shared one."""
    from houndarr.routes import admin

    assert admin.get_templates is get_templates


@pytest.mark.pinning()
def test_changelog_module_uses_shared_templates() -> None:
    """routes.changelog.get_templates is the same callable as the shared one."""
    from houndarr.routes import changelog

    assert changelog.get_templates is get_templates


@pytest.mark.pinning()
def test_update_check_module_uses_shared_templates() -> None:
    """routes.update_check.get_templates is the same callable as the shared one."""
    from houndarr.routes import update_check

    assert update_check.get_templates is get_templates


@pytest.mark.pinning()
def test_settings_helpers_uses_shared_templates() -> None:
    """routes.settings._helpers.get_templates is the same callable as the shared one."""
    from houndarr.routes.settings import _helpers

    assert _helpers.get_templates is get_templates

"""Centralised Jinja2Templates singleton for the route layer.

Every route that renders HTML (``pages``, ``admin``, ``changelog``,
``update_check``, ``settings``) goes through :func:`get_templates`
so template autoescape settings and filter registrations are
symmetric across the five call sites, and new filters register in
one place.  The test conftest that builds its own Jinja
environment (``tests/test_templates/conftest.py``) stays separate;
the production singleton and the test environment are two distinct
things.

The filter registration uses deferred imports so ``_templates.py``
does not drag ``changelog`` or ``update_check`` into the module
import graph at top level; both modules depend on
:func:`get_templates` in turn.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_templates: Jinja2Templates | None = None


def get_templates() -> Jinja2Templates:
    """Return the shared, lazily-initialised Jinja2Templates instance.

    On first call the templates directory is resolved relative to this
    file (``src/houndarr/routes/_templates.py -> ../templates``), the
    environment is constructed, and the custom filters are registered.
    Subsequent calls return the cached instance without re-running the
    setup.

    Tests that want to reset the environment (to install a mock
    filter, swap the template directory, or clear module state
    between runs) can overwrite :data:`_templates` via
    ``module._templates = None`` before calling :func:`get_templates`
    again.
    """
    global _templates  # noqa: PLW0603
    if _templates is None:
        templates_dir = Path(__file__).resolve().parent.parent / "templates"
        _templates = Jinja2Templates(directory=str(templates_dir))
        _register_filters(_templates)
    return _templates


def _register_filters(templates: Jinja2Templates) -> None:
    """Register the custom Jinja filters the route templates rely on.

    Imports happen inside the function body so constructing this
    module does not pull in the filter owners at import time; those
    modules in turn import :func:`get_templates`.

    Args:
        templates: The :class:`Jinja2Templates` instance to attach
            filters to.  Callers should have just constructed it;
            repeated calls overwrite the same keys with the same
            callables and are inert.
    """
    from houndarr.routes.changelog import _render_changelog_bullet
    from houndarr.routes.update_check import _timeago

    templates.env.filters["changelog_bullet"] = _render_changelog_bullet
    templates.env.filters["timeago"] = _timeago

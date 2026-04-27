"""Render-harness fixtures used by Track A.22 pinning tests.

Track E (Jinja macros) will rewrite every partial listed here.  The
pinning harness renders the partial against a stable context and
returns the raw HTML so callers can assert structural markers survive
the macro extraction.

Rendering is intentionally loose: we compare on HTML substrings, not
byte equality, because Jinja whitespace is sensitive to adjacent
changes that Track E may legitimately introduce.  The markers we pin
are the class names, data-* attributes, and visible text labels that
the HTMX client and CSS rely on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "houndarr" / "templates"


@pytest.fixture()
def jinja_env() -> Environment:
    """Plain Jinja2 environment rooted at the source templates/ directory."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    # Register the custom changelog_bullet filter so partials/changelog_content.html renders.
    from houndarr.routes.changelog import _render_changelog_bullet

    env.filters["changelog_bullet"] = _render_changelog_bullet
    return env


def _render(env: Environment, template_name: str, **context: Any) -> str:
    return env.get_template(template_name).render(**context)


@pytest.fixture()
def render(jinja_env: Environment):
    """Render a template by name with the provided context."""

    def _inner(template_name: str, **context: Any) -> str:
        return _render(jinja_env, template_name, **context)

    return _inner

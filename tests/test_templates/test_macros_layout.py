"""Byte-equal output pinning for the macro in `_macros/layout.html`.

The admin_section macro wraps the section + heading shell that the
four Admin sub-section partials repeat verbatim
(security / updates / maintenance / danger).  Each test invokes
the macro in isolation and asserts the exact bytes Jinja emits.

Like the other macro pinning suites under tests/test_templates/, the
macro output is not the same as the full post-migration consumer
HTML (consumers carry ambient template indentation that the macro
does not).  Consumer-level integration is asserted via the Track E
gate.  What this suite locks down is every attribute, every
rendered class string, the title_color override branch the danger
sub-section depends on, and the section-id placement so a future
edit cannot silently drop the space-y-3 wrapper, swap the heading
colour default, or break the HTMX swap targets that point at
#admin-X.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/layout.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja source snippet (the caller is
        responsible for the `{% import %}` and `{% call %}` boilerplate
        because every section is rendered via a call-block body) and
        returns the rendered HTML exactly as the engine produced it.
    """

    def _inner(src: str) -> str:
        return jinja_env.from_string(src).render()

    return _inner


_IMPORT = '{% import "_macros/layout.html" as layout %}'


class TestAdminSectionDefault:
    """admin_section without a title_color override uses the slate-200 heading."""

    def test_default_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call layout.admin_section(id="admin-x", title="Title") %}'
            "BODY{% endcall %}"
        )
        expected = (
            '<section id="admin-x" class="space-y-3">\n'
            '  <div class="flex flex-wrap items-center justify-between gap-2 mb-3">\n'
            '    <h3 class="text-base font-semibold text-slate-200">Title</h3>\n'
            "  </div>\n"
            "BODY\n"
            "</section>"
        )
        assert render_macro(src) == expected


class TestAdminSectionConsumerCallSites:
    """Each migrated sub-section renders byte-equal to its hand-built post-refactor form."""

    def test_security_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call layout.admin_section(id="admin-security", title="Security") %}'
            "B{% endcall %}"
        )
        expected = (
            '<section id="admin-security" class="space-y-3">\n'
            '  <div class="flex flex-wrap items-center justify-between gap-2 mb-3">\n'
            '    <h3 class="text-base font-semibold text-slate-200">Security</h3>\n'
            "  </div>\n"
            "B\n"
            "</section>"
        )
        assert render_macro(src) == expected

    def test_updates_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call layout.admin_section(id="admin-updates", title="Updates") %}'
            "B{% endcall %}"
        )
        expected = (
            '<section id="admin-updates" class="space-y-3">\n'
            '  <div class="flex flex-wrap items-center justify-between gap-2 mb-3">\n'
            '    <h3 class="text-base font-semibold text-slate-200">Updates</h3>\n'
            "  </div>\n"
            "B\n"
            "</section>"
        )
        assert render_macro(src) == expected

    def test_maintenance_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call layout.admin_section(id="admin-maintenance", '
            'title="Maintenance") %}B{% endcall %}'
        )
        expected = (
            '<section id="admin-maintenance" class="space-y-3">\n'
            '  <div class="flex flex-wrap items-center justify-between gap-2 mb-3">\n'
            '    <h3 class="text-base font-semibold text-slate-200">Maintenance</h3>\n'
            "  </div>\n"
            "B\n"
            "</section>"
        )
        assert render_macro(src) == expected

    def test_danger_overrides_title_color_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call layout.admin_section(id="admin-danger", '
            'title="Danger zone", title_color="text-danger/90") %}B{% endcall %}'
        )
        expected = (
            '<section id="admin-danger" class="space-y-3">\n'
            '  <div class="flex flex-wrap items-center justify-between gap-2 mb-3">\n'
            '    <h3 class="text-base font-semibold text-danger/90">Danger zone</h3>\n'
            "  </div>\n"
            "B\n"
            "</section>"
        )
        assert render_macro(src) == expected


class TestAdminSectionStructuralInvariants:
    """The wrapper structure HTMX swap targets and the JS controller depend on."""

    def test_section_id_placed_on_outer_section(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call layout.admin_section(id="admin-z", title="Z") %}body{% endcall %}'
        result = render_macro(src)
        # The id must sit on the OUTER <section>, not on the header div,
        # so HTMX hx-target="#admin-z" picks the correct swap target.
        assert result.startswith('<section id="admin-z"')

    def test_caller_body_lands_after_header(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call layout.admin_section(id="admin-z", title="Z") %}MARK{% endcall %}'
        result = render_macro(src)
        header_close = result.index("</div>")
        marker = result.index("MARK")
        section_close = result.index("</section>")
        assert header_close < marker < section_close

    def test_h3_renders_with_full_class_string(self, render_macro: Callable[[str], str]) -> None:
        # The heading class string is text-base + font-semibold + colour.
        # Drop one of the three and the visual surface differs even if the
        # tests pass other assertions; pin the full string.
        src = (
            _IMPORT + '{% call layout.admin_section(id="x", title="T", title_color="text-x") %}'
            "b{% endcall %}"
        )
        result = render_macro(src)
        assert 'class="text-base font-semibold text-x">T</h3>' in result

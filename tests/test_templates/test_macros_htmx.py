"""Byte-equal output pinning for each macro in `_macros/htmx.html`.

Each test invokes a single macro in isolation and asserts the exact
bytes Jinja emits.  The whole point of these macros is to centralise
the four shell-fetch wire attributes (`hx-get`, `hx-target`,
`hx-swap`, `hx-push-url`) plus the `href` fallback, so the pinning
enumerates every branch that could silently drop one of those.

Consumer-level pinning (markup substrings under
`partials/shell_nav_links.html` + the base.html logo) lives in
``test_pinned_render.py`` and matches on class-string and
attribute substrings, which is what the HTMX client and app.js's
active-state code actually depend on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/htmx.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja source fragment invoking a
        macro from the ``hx`` namespace (e.g. ``"{{ hx.shell_nav_link(...) }}"``
        or a ``{% call %}`` block) and returns the rendered HTML exactly
        as the engine produced it.  The fragment is prepended with
        ``{% import '_macros/htmx.html' as hx %}`` so every caller shares
        the same alias.
    """

    def _inner(fragment: str) -> str:
        src = "{% import '_macros/htmx.html' as hx %}" + fragment
        return jinja_env.from_string(src).render()

    return _inner


class TestShellNavLink:
    """Pin the default pill variant output (desktop header)."""

    @pytest.mark.parametrize(
        ("route", "label"),
        [
            ("/", "Dashboard"),
            ("/logs", "Logs"),
            ("/settings", "Settings"),
        ],
    )
    def test_active_byte_equal(
        self,
        render_macro: Callable[[str], str],
        route: str,
        label: str,
    ) -> None:
        expected = (
            f'<a href="{route}"\n'
            f'   data-shell-nav="true"\n'
            f'   data-shell-route="{route}"\n'
            f'   hx-get="{route}"\n'
            f'   hx-target="#app-content"\n'
            f'   hx-swap="innerHTML"\n'
            f'   hx-push-url="true"\n'
            f'   class="shell-nav-link pill-nav__tab pill-nav__tab--active">\n'
            f"  {label}\n"
            f"</a>"
        )
        got = render_macro(
            "{{ hx.shell_nav_link(" + repr(route) + ", " + repr(label) + ", True) }}"
        )
        assert got == expected

    @pytest.mark.parametrize(
        ("route", "label"),
        [
            ("/", "Dashboard"),
            ("/logs", "Logs"),
            ("/settings", "Settings"),
        ],
    )
    def test_inactive_byte_equal(
        self,
        render_macro: Callable[[str], str],
        route: str,
        label: str,
    ) -> None:
        expected = (
            f'<a href="{route}"\n'
            f'   data-shell-nav="true"\n'
            f'   data-shell-route="{route}"\n'
            f'   hx-get="{route}"\n'
            f'   hx-target="#app-content"\n'
            f'   hx-swap="innerHTML"\n'
            f'   hx-push-url="true"\n'
            f'   class="shell-nav-link pill-nav__tab">\n'
            f"  {label}\n"
            f"</a>"
        )
        got = render_macro(
            "{{ hx.shell_nav_link(" + repr(route) + ", " + repr(label) + ", False) }}"
        )
        assert got == expected

    def test_active_class_differs_from_inactive(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        active = render_macro("{{ hx.shell_nav_link('/', 'Dashboard', True) }}")
        inactive = render_macro("{{ hx.shell_nav_link('/', 'Dashboard', False) }}")
        assert "pill-nav__tab--active" in active
        assert "pill-nav__tab--active" not in inactive
        assert "pill-nav__tab" in inactive
        assert "pill-nav__tab" in active

    def test_hx_wire_attributes_always_present(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        got = render_macro("{{ hx.shell_nav_link('/logs', 'Logs', False) }}")
        assert 'hx-get="/logs"' in got
        assert 'hx-target="#app-content"' in got
        assert 'hx-swap="innerHTML"' in got
        assert 'hx-push-url="true"' in got

    def test_data_shell_hooks_always_present(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        got = render_macro("{{ hx.shell_nav_link('/settings', 'Settings', True) }}")
        assert 'data-shell-nav="true"' in got
        assert 'data-shell-route="/settings"' in got


class TestShellNavLinkMobileVariant:
    """Pin the drawer-row variant output used inside #mobile-nav-menu.

    Wire attributes stay identical to the pill variant; only the class
    string diverges so the drawer keeps its slate-row treatment instead
    of rendering cramped pills on a narrow viewport.
    """

    @pytest.mark.parametrize(
        ("route", "label"),
        [
            ("/", "Dashboard"),
            ("/logs", "Logs"),
            ("/settings", "Settings"),
        ],
    )
    def test_active_byte_equal(
        self,
        render_macro: Callable[[str], str],
        route: str,
        label: str,
    ) -> None:
        expected = (
            f'<a href="{route}"\n'
            f'   data-shell-nav="true"\n'
            f'   data-shell-route="{route}"\n'
            f'   hx-get="{route}"\n'
            f'   hx-target="#app-content"\n'
            f'   hx-swap="innerHTML"\n'
            f'   hx-push-url="true"\n'
            f'   class="shell-nav-link px-3 py-2 rounded-container '
            f'text-sm font-medium bg-surface-3 text-white">\n'
            f"  {label}\n"
            f"</a>"
        )
        got = render_macro(
            "{{ hx.shell_nav_link("
            + repr(route)
            + ", "
            + repr(label)
            + ", True, variant='mobile') }}"
        )
        assert got == expected

    @pytest.mark.parametrize(
        ("route", "label"),
        [
            ("/", "Dashboard"),
            ("/logs", "Logs"),
            ("/settings", "Settings"),
        ],
    )
    def test_inactive_byte_equal(
        self,
        render_macro: Callable[[str], str],
        route: str,
        label: str,
    ) -> None:
        expected = (
            f'<a href="{route}"\n'
            f'   data-shell-nav="true"\n'
            f'   data-shell-route="{route}"\n'
            f'   hx-get="{route}"\n'
            f'   hx-target="#app-content"\n'
            f'   hx-swap="innerHTML"\n'
            f'   hx-push-url="true"\n'
            f'   class="shell-nav-link px-3 py-2 rounded-container '
            f'text-sm font-medium text-slate-400 hover:text-white hover:bg-surface-2">\n'
            f"  {label}\n"
            f"</a>"
        )
        got = render_macro(
            "{{ hx.shell_nav_link("
            + repr(route)
            + ", "
            + repr(label)
            + ", False, variant='mobile') }}"
        )
        assert got == expected


class TestHxShellFetch:
    def test_minimal_call_byte_equal(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        expected = (
            '<a href="/"\n'
            '   hx-get="/"\n'
            '   hx-target="#app-content"\n'
            '   hx-swap="innerHTML"\n'
            '   hx-push-url="true"\n'
            '   class="">HOME</a>'
        )
        got = render_macro(
            "{% call hx.hx_shell_fetch('/') %}HOME{% endcall %}",
        )
        assert got == expected

    def test_explicit_class_byte_equal(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        expected = (
            '<a href="/logs"\n'
            '   hx-get="/logs"\n'
            '   hx-target="#app-content"\n'
            '   hx-swap="innerHTML"\n'
            '   hx-push-url="true"\n'
            '   class="btn btn-primary">child</a>'
        )
        got = render_macro(
            "{% call hx.hx_shell_fetch('/logs', class_='btn btn-primary') %}child{% endcall %}"
        )
        assert got == expected

    def test_multiline_caller_content_preserved(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        # The base.html header-logo consumer wraps an img plus a
        # trailing text label; this test pins that the macro passes
        # multi-line caller content through unchanged, since byte
        # drift here would risk an alignment shift in the nav shell.
        expected = (
            '<a href="/"\n'
            '   hx-get="/"\n'
            '   hx-target="#app-content"\n'
            '   hx-swap="innerHTML"\n'
            '   hx-push-url="true"\n'
            '   class="logo-class">\n'
            '  <img alt="x" />\n'
            "  Houndarr\n"
            "</a>"
        )
        got = render_macro(
            "{% call hx.hx_shell_fetch('/', class_='logo-class') %}\n"
            '  <img alt="x" />\n'
            "  Houndarr\n"
            "{% endcall %}"
        )
        assert got == expected

    def test_hx_wire_attributes_always_present(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        got = render_macro("{% call hx.hx_shell_fetch('/') %}x{% endcall %}")
        assert 'hx-get="/"' in got
        assert 'hx-target="#app-content"' in got
        assert 'hx-swap="innerHTML"' in got
        assert 'hx-push-url="true"' in got

    def test_href_matches_hx_get(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        got = render_macro("{% call hx.hx_shell_fetch('/logs') %}x{% endcall %}")
        assert 'href="/logs"' in got
        assert 'hx-get="/logs"' in got

    def test_default_class_is_empty_string(
        self,
        render_macro: Callable[[str], str],
    ) -> None:
        got = render_macro("{% call hx.hx_shell_fetch('/') %}x{% endcall %}")
        assert 'class=""' in got

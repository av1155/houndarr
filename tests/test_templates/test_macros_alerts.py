"""Byte-equal output pinning for the macro in `_macros/alerts.html`.

The alert macro renders the daisyUI soft-banner shape used by both
the base.html flash toasts and the inline form-error / account-status
banners.  Each test invokes the macro in isolation and asserts the
exact bytes Jinja emits.

Consumer-level integration (the full rendered HTML including
ambient template indentation) is asserted via class-string
substring tests in ``test_pinned_render.py`` and the macro
inventory gate.  What this suite locks down is every class string,
attribute, and branch of the macro so a future edit cannot silently
drop a class, swap an attribute, or flip a default that the CSS
component layer relies on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/alerts.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja source snippet (the caller is
        responsible for the `{% import %}` and `{% call %}` boilerplate
        because every alert is rendered via a call-block body) and
        returns the rendered HTML exactly as the engine produced it.
    """

    def _inner(src: str) -> str:
        return jinja_env.from_string(src).render()

    return _inner


_IMPORT = '{% import "_macros/alerts.html" as alerts %}'


class TestAlertVariants:
    """alert renders one of four daisyUI soft variants."""

    @pytest.mark.parametrize(
        ("variant", "alert_class"),
        [
            ("error", "alert-error"),
            ("success", "alert-success"),
            ("warning", "alert-warning"),
            ("info", "alert-info"),
        ],
    )
    def test_each_variant_byte_equal(
        self,
        render_macro: Callable[[str], str],
        variant: str,
        alert_class: str,
    ) -> None:
        src = _IMPORT + '{% call alerts.alert("' + variant + '") %}body{% endcall %}'
        expected = f'<div class="alert alert-soft {alert_class} text-sm">\n  body\n</div>'
        assert render_macro(src) == expected


class TestAlertConsumerCallSites:
    """Each migrated call site renders byte-equal to its hand-built post-refactor form."""

    def test_base_flash_error_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call alerts.alert("error", extra="text-slate-100 text-sm", '
            'id="flash-error", role="alert") %}Error message{% endcall %}'
        )
        expected = (
            '<div id="flash-error" class="alert alert-soft alert-error '
            'text-slate-100 text-sm" role="alert">\n'
            "  Error message\n"
            "</div>"
        )
        assert render_macro(src) == expected

    def test_base_flash_success_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call alerts.alert("success", extra="text-slate-100 text-sm", '
            'id="flash-success", role="alert") %}Success message{% endcall %}'
        )
        expected = (
            '<div id="flash-success" class="alert alert-soft alert-success '
            'text-slate-100 text-sm" role="alert">\n'
            "  Success message\n"
            "</div>"
        )
        assert render_macro(src) == expected

    def test_instance_form_inline_error_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call alerts.alert("error", prefix="mb-3") %}'
            "Connection failed{% endcall %}"
        )
        expected = (
            '<div class="mb-3 alert alert-soft alert-error text-sm">\n  Connection failed\n</div>'
        )
        assert render_macro(src) == expected

    def test_settings_content_inline_error_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call alerts.alert("error", prefix="mb-4", extra="text-sm font-sans") %}'
            "Invalid input{% endcall %}"
        )
        expected = (
            '<div class="mb-4 alert alert-soft alert-error text-sm font-sans">\n'
            "  Invalid input\n"
            "</div>"
        )
        assert render_macro(src) == expected

    def test_admin_security_account_error_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call alerts.alert("error", prefix="min-[971px]:col-span-3", '
            'extra="text-sm font-sans") %}Wrong password{% endcall %}'
        )
        expected = (
            '<div class="min-[971px]:col-span-3 alert alert-soft alert-error '
            'text-sm font-sans">\n'
            "  Wrong password\n"
            "</div>"
        )
        assert render_macro(src) == expected

    def test_admin_security_account_success_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call alerts.alert("success", prefix="min-[971px]:col-span-3", '
            'extra="text-sm font-sans") %}Password updated{% endcall %}'
        )
        expected = (
            '<div class="min-[971px]:col-span-3 alert alert-soft alert-success '
            'text-sm font-sans">\n'
            "  Password updated\n"
            "</div>"
        )
        assert render_macro(src) == expected


class TestAlertOptionalAttributes:
    """Every optional attribute is omitted by default."""

    def test_no_id_attribute_when_id_omitted(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call alerts.alert("error") %}x{% endcall %}'
        result = render_macro(src)
        assert " id=" not in result

    def test_no_role_attribute_when_role_omitted(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call alerts.alert("error") %}x{% endcall %}'
        result = render_macro(src)
        assert " role=" not in result

    def test_no_prefix_when_prefix_empty(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call alerts.alert("error") %}x{% endcall %}'
        result = render_macro(src)
        # Empty prefix: class string starts directly with the alert classes,
        # no leading whitespace before "alert".
        assert 'class="alert alert-soft' in result

    def test_extra_empty_omits_trailing_class(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call alerts.alert("info", extra="") %}x{% endcall %}'
        expected = '<div class="alert alert-soft alert-info">\n  x\n</div>'
        assert render_macro(src) == expected

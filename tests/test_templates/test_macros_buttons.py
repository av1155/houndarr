"""Byte-equal output pinning for the macro in `_macros/buttons.html`.

The btn macro covers the four named low-variance call sites listed
in plan section 6: changelog_modal, confirm_dialog, admin/maintenance,
and admin/danger.  Each test invokes the macro in isolation and
asserts the exact bytes Jinja emits.

Like the other macro pinning suites under tests/test_templates/, the
macro output is not the same as the full post-migration consumer
HTML (consumers carry ambient template indentation that the macro
does not).  Consumer-level integration is asserted via the Track E
gate and via the Track A.22 render harness.  What this suite locks
down is every variant, every size, the boolean / scalar / None
attribute branches, the extra class slot, and the variant / size
fallback paths so a future edit cannot silently drop a class, swap
an attribute order, or flip a default that input.css or the JS
controllers rely on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/buttons.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja source snippet (the caller is
        responsible for the `{% import %}` and `{% call %}` boilerplate
        because every button is rendered via a call-block body) and
        returns the rendered HTML exactly as the engine produced it.
    """

    def _inner(src: str) -> str:
        return jinja_env.from_string(src).render()

    return _inner


_IMPORT = '{% import "_macros/buttons.html" as buttons %}'


class TestBtnVariants:
    """btn renders one of four daisyUI palette variants."""

    @pytest.mark.parametrize(
        ("variant", "variant_classes"),
        [
            ("primary", "btn btn-primary"),
            ("soft-neutral", "btn btn-soft btn-neutral"),
            ("soft-error", "btn btn-soft btn-error"),
            ("soft-success", "btn btn-soft btn-success"),
        ],
    )
    def test_each_variant_byte_equal(
        self,
        render_macro: Callable[[str], str],
        variant: str,
        variant_classes: str,
    ) -> None:
        src = _IMPORT + '{% call buttons.btn(variant="' + variant + '") %}label{% endcall %}'
        expected = f'<button class="{variant_classes} text-sm px-3 py-1.5">\n  label\n</button>'
        assert render_macro(src) == expected

    def test_unknown_variant_falls_back_to_primary(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = _IMPORT + '{% call buttons.btn(variant="not-a-variant") %}x{% endcall %}'
        result = render_macro(src)
        assert "btn btn-primary" in result


class TestBtnSizes:
    """btn renders one of three size combinations."""

    @pytest.mark.parametrize(
        ("size", "size_classes"),
        [
            ("sm", "text-xs px-3 py-1.5"),
            ("md", "text-sm px-3 py-1.5"),
            ("lg", "text-sm px-3 py-2"),
        ],
    )
    def test_each_size_byte_equal(
        self,
        render_macro: Callable[[str], str],
        size: str,
        size_classes: str,
    ) -> None:
        src = _IMPORT + '{% call buttons.btn(size="' + size + '") %}label{% endcall %}'
        expected = f'<button class="btn btn-primary {size_classes}">\n  label\n</button>'
        assert render_macro(src) == expected

    def test_unknown_size_falls_back_to_md(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call buttons.btn(size="not-a-size") %}x{% endcall %}'
        result = render_macro(src)
        assert "text-sm px-3 py-1.5" in result


class TestBtnAttrs:
    """attrs is iterated in insertion order; bool / None / scalar branches each render."""

    def test_no_attrs_renders_no_extra_attributes(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + "{% call buttons.btn() %}label{% endcall %}"
        expected = '<button class="btn btn-primary text-sm px-3 py-1.5">\n  label\n</button>'
        assert render_macro(src) == expected

    def test_scalar_attr_renders_with_value(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call buttons.btn(attrs={"type": "submit"}) %}x{% endcall %}'
        expected = (
            '<button type="submit" class="btn btn-primary text-sm px-3 py-1.5">\n  x\n</button>'
        )
        assert render_macro(src) == expected

    def test_boolean_true_renders_attr_name_only(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + "{% call buttons.btn(attrs={"
            + '"type": "button", "autofocus": true'
            + "}) %}x{% endcall %}"
        )
        expected = (
            '<button type="button" autofocus class="btn btn-primary text-sm px-3 py-1.5">\n'
            "  x\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_boolean_false_omits_attr(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + "{% call buttons.btn(attrs={"
            + '"type": "button", "autofocus": false'
            + "}) %}x{% endcall %}"
        )
        result = render_macro(src)
        assert " autofocus" not in result
        assert ' type="button"' in result

    def test_none_value_omits_attr(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + "{% call buttons.btn(attrs={"
            + '"type": "button", "data-x": none'
            + "}) %}x{% endcall %}"
        )
        result = render_macro(src)
        assert "data-x" not in result
        assert ' type="button"' in result

    def test_attr_order_preserved(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + "{% call buttons.btn(attrs={"
            + '"data-z": "1", "data-a": "2", "data-m": "3"'
            + "}) %}x{% endcall %}"
        )
        result = render_macro(src)
        # Attribute order matches the dict insertion order.
        z_at = result.index("data-z")
        a_at = result.index("data-a")
        m_at = result.index("data-m")
        assert z_at < a_at < m_at


class TestBtnExtra:
    """extra is appended after the variant and size class strings."""

    def test_extra_appended_after_size(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call buttons.btn(extra="font-semibold shrink-0") %}x{% endcall %}'
        expected = (
            '<button class="btn btn-primary text-sm px-3 py-1.5 '
            'font-semibold shrink-0">\n'
            "  x\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_empty_extra_renders_no_trailing_space(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = _IMPORT + '{% call buttons.btn(extra="") %}x{% endcall %}'
        result = render_macro(src)
        assert 'class="btn btn-primary text-sm px-3 py-1.5"' in result


class TestBtnConsumerCallSites:
    """Each migrated call site renders byte-equal to its hand-built post-refactor form."""

    def test_changelog_modal_dont_show_again_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="soft-neutral", size="md", attrs={'
            + '"type": "button",'
            + '"data-changelog-disable": "true",'
            + '"hx-post": "/settings/changelog/disable",'
            + '"hx-swap": "none",'
            + "}) %}Don't show again{% endcall %}"
        )
        expected = (
            '<button type="button" data-changelog-disable="true" '
            'hx-post="/settings/changelog/disable" hx-swap="none" '
            'class="btn btn-soft btn-neutral text-sm px-3 py-1.5">\n'
            "  Don't show again\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_changelog_modal_got_it_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="primary", size="md", '
            + 'extra="font-semibold", attrs={'
            + '"type": "button",'
            + '"autofocus": true,'
            + '"data-changelog-dismiss": "true",'
            + '"hx-post": "/settings/changelog/dismiss",'
            + '"hx-swap": "none",'
            + "}) %}Got it{% endcall %}"
        )
        expected = (
            '<button type="button" autofocus data-changelog-dismiss="true" '
            'hx-post="/settings/changelog/dismiss" hx-swap="none" '
            'class="btn btn-primary text-sm px-3 py-1.5 font-semibold">\n'
            "  Got it\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_confirm_dialog_cancel_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="soft-neutral", size="lg", attrs={'
            + '"type": "button",'
            + '"data-dismiss-confirm": true,'
            + "}) %}Cancel{% endcall %}"
        )
        expected = (
            '<button type="button" data-dismiss-confirm '
            'class="btn btn-soft btn-neutral text-sm px-3 py-2">\n'
            "  Cancel\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_confirm_dialog_confirm_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="soft-error", size="lg", '
            + 'extra="font-medium", attrs={'
            + '"id": "confirm-go",'
            + '"type": "submit",'
            + "}) %}Confirm{% endcall %}"
        )
        expected = (
            '<button id="confirm-go" type="submit" '
            'class="btn btn-soft btn-error text-sm px-3 py-2 font-medium">\n'
            "  Confirm\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_maintenance_reset_settings_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="soft-neutral", size="sm", '
            + 'extra="font-medium shrink-0", attrs={'
            + '"type": "button",'
            + '"data-confirm-reset": "instances",'
            + "}) %}Reset settings{% endcall %}"
        )
        expected = (
            '<button type="button" data-confirm-reset="instances" '
            'class="btn btn-soft btn-neutral text-xs px-3 py-1.5 '
            'font-medium shrink-0">\n'
            "  Reset settings\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_maintenance_clear_logs_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="soft-neutral", size="sm", '
            + 'extra="font-medium shrink-0", attrs={'
            + '"type": "button",'
            + '"data-confirm-reset": "logs",'
            + "}) %}Clear logs{% endcall %}"
        )
        expected = (
            '<button type="button" data-confirm-reset="logs" '
            'class="btn btn-soft btn-neutral text-xs px-3 py-1.5 '
            'font-medium shrink-0">\n'
            "  Clear logs\n"
            "</button>"
        )
        assert render_macro(src) == expected

    def test_danger_factory_reset_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT
            + '{% call buttons.btn(variant="soft-error", size="sm", '
            + 'extra="font-medium shrink-0 px-4", attrs={'
            + '"type": "button",'
            + '"data-confirm-reset": "factory",'
            + "}) %}Factory reset{% endcall %}"
        )
        expected = (
            '<button type="button" data-confirm-reset="factory" '
            'class="btn btn-soft btn-error text-xs px-3 py-1.5 '
            'font-medium shrink-0 px-4">\n'
            "  Factory reset\n"
            "</button>"
        )
        assert render_macro(src) == expected

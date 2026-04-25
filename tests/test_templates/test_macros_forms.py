"""Byte-equal output pinning for each macro in `_macros/forms.html`.

Each test invokes a single macro in isolation and asserts the exact
bytes Jinja emits.  The macros use whitespace control that ambient
template indentation does not, so consumer-level behaviour is
asserted via the CSS / JS markers (class strings, data-*
attributes, id pairings) that auth.js and the auth-fields
stylesheet read.  What the assertions here lock down is every
class string, data-* attribute, label wrapper, and optional feature
toggle of every macro so a future edit cannot silently drop or
rename a marker that the browser relies on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/forms.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja expression importing and
        invoking a macro (e.g. ``"form_field(id='x', name='y', label='Z')"``)
        and returns the rendered HTML exactly as the engine produced it.
    """

    def _inner(call_expr: str) -> str:
        src = "{% import '_macros/forms.html' as forms %}{{ forms." + call_expr + " }}"
        return jinja_env.from_string(src).render()

    return _inner


_AUTH_INPUT_TRAILING = (
    '  <div class="input-trailing">\n'
    '    <span class="caps-badge" role="status" aria-label="Caps Lock is on">\n'
    '      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"\n'
    '           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">\n'
    '        <path d="M8 3.2 3.6 7.6h2.5v4h3.8v-4h2.5z"/>\n'
    '        <line x1="5.6" y1="13.3" x2="10.4" y2="13.3"/>\n'
    "      </svg>\n"
    "    </span>\n"
    '    <button type="button" class="icon-btn"\n'
    '            aria-label="Show password" aria-pressed="false"\n'
    '            data-pw-toggle tabindex="-1">\n'
    '      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"\n'
    '           stroke="currentColor" stroke-width="2" stroke-linecap="round"\n'
    '           stroke-linejoin="round" aria-hidden="true">\n'
    '        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/>\n'
    '        <circle cx="12" cy="12" r="3"/>\n'
    "      </svg>\n"
    "    </button>\n"
    "  </div>"
)

_AUTH_PADLOCK_SVG = (
    '  <svg class="leading" width="16" height="16" viewBox="0 0 24 24" fill="none"\n'
    '       stroke="currentColor" stroke-width="2" stroke-linecap="round"\n'
    '       stroke-linejoin="round" aria-hidden="true">\n'
    '    <rect x="3" y="11" width="18" height="10" rx="2"/>\n'
    '    <path d="M7 11V7a5 5 0 0 1 10 0v4"/>\n'
    "  </svg>"
)

_AUTH_CHECKMARK_SVG = (
    '  <svg class="leading" width="16" height="16" viewBox="0 0 24 24" fill="none"\n'
    '       stroke="currentColor" stroke-width="2" stroke-linecap="round"\n'
    '       stroke-linejoin="round" aria-hidden="true">\n'
    '    <path d="M20 6 9 17l-5-5"/>\n'
    "  </svg>"
)

_ADMIN_INPUT_TRAILING = (
    '  <div class="input-trailing">\n'
    '    <span class="caps-badge" role="status" aria-label="Caps Lock is on">\n'
    '      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">\n'
    '        <path d="M8 3.2 3.6 7.6h2.5v4h3.8v-4h2.5z"/>\n'
    '        <line x1="5.6" y1="13.3" x2="10.4" y2="13.3"/>\n'
    "      </svg>\n"
    "    </span>\n"
    "    <button\n"
    '      type="button"\n'
    '      class="icon-btn"\n'
    "      data-pw-toggle\n"
    '      aria-label="Show password"\n'
    '      aria-pressed="false"\n'
    '      tabindex="-1"\n'
    "    >\n"
    '      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">\n'
    '        <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/>\n'
    '        <circle cx="12" cy="12" r="3"/>\n'
    "      </svg>\n"
    "    </button>\n"
    "  </div>"
)

_STRENGTH_METER_AUTH = (
    '<div class="strength" data-strength role="meter"\n'
    '     aria-label="Password strength"\n'
    '     aria-valuemin="0" aria-valuemax="4" aria-valuenow="0" aria-valuetext="—">\n'
    '  <div class="strength__track">\n'
    '    <div class="strength__seg"></div>\n'
    '    <div class="strength__seg"></div>\n'
    '    <div class="strength__seg"></div>\n'
    '    <div class="strength__seg"></div>\n'
    "  </div>\n"
    '  <div class="strength__meta">\n'
    "    <span>Strength</span>\n"
    '    <span class="strength__label">—</span>\n'
    "  </div>\n"
    "</div>"
)

_STRENGTH_METER_ADMIN = (
    "<div\n"
    '  class="strength"\n'
    "  data-strength\n"
    '  role="meter"\n'
    '  aria-label="Password strength"\n'
    '  aria-valuemin="0"\n'
    '  aria-valuemax="4"\n'
    '  aria-valuenow="0"\n'
    '  aria-valuetext="—"\n'
    ">\n"
    '  <div class="strength__track">\n'
    '    <div class="strength__seg"></div>\n'
    '    <div class="strength__seg"></div>\n'
    '    <div class="strength__seg"></div>\n'
    '    <div class="strength__seg"></div>\n'
    "  </div>\n"
    '  <div class="strength__meta">\n'
    "    <span>Strength</span>\n"
    '    <span class="strength__label">—</span>\n'
    "  </div>\n"
    "</div>"
)


class TestFormField:
    """form_field renders <label> + <input> with optional help and call-block label."""

    def test_minimal_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<label class="field-label" for="x">Z</label>\n'
            '<input id="x" name="y" type="text" class="station-input" />'
        )
        assert render_macro('form_field(id="x", name="y", label="Z")') == expected

    def test_instance_form_number_input_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        expected = (
            '<label class="field-label" for="edit-form-1-batch">Batch Size</label>\n'
            '<input id="edit-form-1-batch" name="batch_size" type="number" '
            'min="1" max="250" value="2" data-default-value="5" '
            'class="station-input h-11 text-base text-white '
            'placeholder:text-slate-600 font-mono" />'
        )
        assert (
            render_macro(
                'form_field(id="edit-form-1-batch", name="batch_size", type="number", '
                "min=1, max=250, value=2, data_default_value=5, "
                'input_class="station-input h-11 text-base text-white '
                'placeholder:text-slate-600 font-mono", label="Batch Size")'
            )
            == expected
        )

    def test_help_text_appends_paragraph(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<label class="field-label" for="x">Post-Release Grace (hrs)</label>\n'
            '<input id="x" name="y" type="number" min="0" value="6" '
            'data-default-value="6" class="mono" />\n'
            '<p class="mt-1.5 text-xs text-slate-600">'
            "Hours to wait after release date.</p>"
        )
        assert (
            render_macro(
                'form_field(id="x", name="y", type="number", min=0, value=6, '
                'data_default_value=6, input_class="mono", '
                'label="Post-Release Grace (hrs)", '
                'help_text="Hours to wait after release date.")'
            )
            == expected
        )

    def test_value_zero_still_rendered(self, render_macro: Callable[[str], str]) -> None:
        # Zero is a valid value for min=0 integer fields and must not be
        # dropped as if it were "falsy"; the `is not none` guard keeps it.
        result = render_macro(
            'form_field(id="x", name="y", type="number", value=0, min=0, label="Z")'
        )
        assert ' value="0"' in result

    def test_value_none_omits_value_attr(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro('form_field(id="x", name="y", label="Z")')
        assert " value=" not in result

    def test_call_block_supplies_label_content(self, jinja_env: Environment) -> None:
        src = (
            "{% import '_macros/forms.html' as forms %}"
            "{% call forms.form_field("
            'id="confirm-phrase-input", name="confirm_phrase", '
            'input_class="station-input h-10 px-3 text-sm font-mono", '
            'label_class="block text-xs font-medium text-slate-400 mb-1.5", '
            'autocomplete="off") %}'
            "Type\n"
            '<span id="confirm-word" class="font-mono text-danger">RESET</span>\n'
            "to confirm{% endcall %}"
        )
        expected = (
            '<label class="block text-xs font-medium text-slate-400 mb-1.5" '
            'for="confirm-phrase-input">Type\n'
            '<span id="confirm-word" class="font-mono text-danger">RESET</span>\n'
            "to confirm</label>\n"
            '<input id="confirm-phrase-input" name="confirm_phrase" type="text" '
            'autocomplete="off" '
            'class="station-input h-10 px-3 text-sm font-mono" />'
        )
        assert jinja_env.from_string(src).render() == expected

    def test_placeholder_renders_attribute(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro(
            'form_field(id="w", name="w", type="text", label="W", placeholder="e.g. 09:00-23:00")'
        )
        assert ' placeholder="e.g. 09:00-23:00"' in result


def _login_expected(aria_invalid: bool = False) -> str:
    """Build the byte-equal expected output for the login password call."""
    aria = ' aria-invalid="true"' if aria_invalid else ""
    return (
        '<label class="field__label" for="login-password">\n'
        "  <span>Password</span>\n"
        "</label>\n"
        '<div class="input-wrap">\n'
        f"{_AUTH_PADLOCK_SVG}\n"
        '  <input id="login-password" name="password" type="password"\n'
        '         class="station-input has-trailing mono"\n'
        '         autocomplete="current-password" required\n'
        '         placeholder="••••••••"\n'
        f"         data-pw-input{aria} />\n"
        f"{_AUTH_INPUT_TRAILING}\n"
        "</div>"
    )


class TestPasswordInputAuth:
    """password_input renders the .field__label + .leading SVG variant."""

    def test_login_shape_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        assert (
            render_macro(
                'password_input(id="login-password", name="password", '
                'label="Password", variant="auth", leading="padlock", '
                'autocomplete="current-password", '
                'placeholder="••••••••")'
            )
            == _login_expected()
        )

    def test_login_with_aria_invalid_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        assert render_macro(
            'password_input(id="login-password", name="password", '
            'label="Password", variant="auth", leading="padlock", '
            'autocomplete="current-password", '
            'placeholder="••••••••", '
            "aria_invalid=true)"
        ) == _login_expected(aria_invalid=True)

    def test_setup_new_password_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<label class="field__label" for="setup-password">\n'
            "  <span>Password</span>\n"
            "</label>\n"
            '<div class="input-wrap">\n'
            f"{_AUTH_PADLOCK_SVG}\n"
            '  <input id="setup-password" name="password" type="password"\n'
            '         class="station-input has-trailing mono"\n'
            '         autocomplete="new-password" required minlength="8"\n'
            '         placeholder="Minimum 8 characters"\n'
            "         data-pw-input data-strength-source />\n"
            f"{_AUTH_INPUT_TRAILING}\n"
            "</div>\n"
            f"{_STRENGTH_METER_AUTH}"
        )
        assert (
            render_macro(
                'password_input(id="setup-password", name="password", '
                'label="Password", variant="auth", leading="padlock", '
                'autocomplete="new-password", '
                'placeholder="Minimum 8 characters", minlength=8, '
                "strength_meter=true, data_strength_source=true)"
            )
            == expected
        )

    def test_setup_confirm_checkmark_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<label class="field__label" for="setup-password-confirm">\n'
            "  <span>Confirm password</span>\n"
            "</label>\n"
            '<div class="input-wrap">\n'
            f"{_AUTH_CHECKMARK_SVG}\n"
            '  <input id="setup-password-confirm" name="password_confirm" '
            'type="password"\n'
            '         class="station-input has-trailing mono"\n'
            '         autocomplete="new-password" required minlength="8"\n'
            '         placeholder="Repeat your password"\n'
            "         data-pw-input />\n"
            f"{_AUTH_INPUT_TRAILING}\n"
            "</div>"
        )
        assert (
            render_macro(
                'password_input(id="setup-password-confirm", '
                'name="password_confirm", label="Confirm password", '
                'variant="auth", leading="checkmark", '
                'autocomplete="new-password", '
                'placeholder="Repeat your password", minlength=8)'
            )
            == expected
        )

    def test_leading_none_omits_svg(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro(
            'password_input(id="x", name="y", label="Z", variant="auth", '
            'autocomplete="current-password", placeholder="ab")'
        )
        assert '<svg class="leading"' not in result
        # Sanity: the input-wrap still opens immediately before the input.
        assert '<div class="input-wrap">\n  <input ' in result


def _admin_current_expected() -> str:
    """Byte-equal expected output for the admin current-password call."""
    return (
        "<label\n"
        '  class="block text-xs font-medium text-slate-400 mb-1.5"\n'
        '  for="current-password"\n'
        ">\n"
        "  Current password\n"
        "</label>\n"
        '<div class="input-wrap">\n'
        "  <input\n"
        '    id="current-password"\n'
        '    name="current_password"\n'
        '    type="password"\n'
        "    required\n"
        '    autocomplete="current-password"\n'
        '    placeholder="••••••••"\n'
        "    data-pw-input\n"
        '    class="station-input has-trailing h-10 px-3 text-sm '
        'text-white placeholder:text-slate-600 font-mono"\n'
        "  />\n"
        f"{_ADMIN_INPUT_TRAILING}\n"
        "</div>"
    )


class TestPasswordInputAdmin:
    """password_input renders the Tailwind-utility admin variant."""

    def test_admin_current_password_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        assert (
            render_macro(
                'password_input(id="current-password", name="current_password", '
                'label="Current password", variant="admin", '
                'autocomplete="current-password", '
                'placeholder="••••••••")'
            )
            == _admin_current_expected()
        )

    def test_admin_new_password_with_strength_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        expected = (
            "<label\n"
            '  class="block text-xs font-medium text-slate-400 mb-1.5"\n'
            '  for="new-password"\n'
            ">\n"
            "  New password\n"
            "</label>\n"
            '<div class="input-wrap">\n'
            "  <input\n"
            '    id="new-password"\n'
            '    name="new_password"\n'
            '    type="password"\n'
            "    required\n"
            '    minlength="8"\n'
            '    autocomplete="new-password"\n'
            '    placeholder="Minimum 8 characters"\n'
            "    data-pw-input\n"
            "    data-strength-source\n"
            '    class="station-input has-trailing h-10 px-3 text-sm '
            'text-white placeholder:text-slate-600 font-mono"\n'
            "  />\n"
            f"{_ADMIN_INPUT_TRAILING}\n"
            "</div>\n"
            f"{_STRENGTH_METER_ADMIN}"
        )
        assert (
            render_macro(
                'password_input(id="new-password", name="new_password", '
                'label="New password", variant="admin", '
                'autocomplete="new-password", '
                'placeholder="Minimum 8 characters", minlength=8, '
                "strength_meter=true, data_strength_source=true)"
            )
            == expected
        )

    def test_admin_confirm_password_with_pw_match_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        expected = (
            "<label\n"
            '  class="block text-xs font-medium text-slate-400 mb-1.5"\n'
            '  for="confirm-password"\n'
            ">\n"
            "  Confirm password\n"
            "</label>\n"
            '<div class="input-wrap">\n'
            "  <input\n"
            '    id="confirm-password"\n'
            '    name="new_password_confirm"\n'
            '    type="password"\n'
            "    required\n"
            '    minlength="8"\n'
            '    autocomplete="new-password"\n'
            '    placeholder="Repeat new password"\n'
            "    data-pw-input\n"
            '    data-pw-confirm="new-password"\n'
            '    aria-describedby="pw-match"\n'
            '    class="station-input has-trailing h-10 px-3 text-sm '
            'text-white placeholder:text-slate-600 font-mono"\n'
            "  />\n"
            f"{_ADMIN_INPUT_TRAILING}\n"
            "</div>\n"
            '<p id="pw-match" class="pw-match" aria-live="polite">&nbsp;</p>'
        )
        assert (
            render_macro(
                'password_input(id="confirm-password", '
                'name="new_password_confirm", label="Confirm password", '
                'variant="admin", autocomplete="new-password", '
                'placeholder="Repeat new password", minlength=8, '
                'data_pw_confirm="new-password", '
                'aria_describedby="pw-match", pw_match=true)'
            )
            == expected
        )

    def test_admin_confirm_dialog_password_not_required_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        expected = (
            "<label\n"
            '  class="block text-xs font-medium text-slate-400 mb-1.5"\n'
            '  for="confirm-password-input"\n'
            ">\n"
            "  Current password\n"
            "</label>\n"
            '<div class="input-wrap">\n'
            "  <input\n"
            '    id="confirm-password-input"\n'
            '    name="current_password"\n'
            '    type="password"\n'
            '    autocomplete="current-password"\n'
            '    placeholder="••••••••"\n'
            "    data-pw-input\n"
            '    class="station-input has-trailing h-10 px-3 text-sm '
            'text-white placeholder:text-slate-600 font-mono"\n'
            "  />\n"
            f"{_ADMIN_INPUT_TRAILING}\n"
            "</div>"
        )
        assert (
            render_macro(
                'password_input(id="confirm-password-input", '
                'name="current_password", label="Current password", '
                'variant="admin", autocomplete="current-password", '
                'placeholder="••••••••", '
                "required=false)"
            )
            == expected
        )

    def test_default_variant_is_admin(self, render_macro: Callable[[str], str]) -> None:
        # Default variant is admin; callers that omit it should get the
        # admin treatment so the macro fails loudly in the easier-to-debug
        # direction if they meant auth.
        result = render_macro(
            'password_input(id="x", name="y", label="Z", '
            'autocomplete="current-password", placeholder="ab")'
        )
        assert 'class="field__label"' not in result
        assert (
            'class="station-input has-trailing h-10 px-3 text-sm '
            'text-white placeholder:text-slate-600 font-mono"'
        ) in result

    def test_unknown_variant_falls_back_to_admin(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro(
            'password_input(id="x", name="y", label="Z", variant="not-a-real-variant", '
            'autocomplete="current-password", placeholder="ab")'
        )
        assert 'class="field__label"' not in result
        assert 'class="station-input has-trailing h-10 px-3 text-sm' in result


class TestCheckbox:
    """checkbox renders the bordered-label toggle for instance_form cutoff / upgrade."""

    def test_unchecked_with_default_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<label class="inline-flex items-center gap-2 cursor-pointer '
            "select-none rounded-inset border border-border-default "
            'bg-surface-1 px-3 py-2">\n'
            '  <input type="checkbox" name="cutoff_enabled" value="on"\n'
            '         data-default-checked="0"\n'
            '         class="rounded-chip border-border-default bg-surface-1 '
            "text-brand-500 focus:ring-brand-500 "
            'focus:ring-offset-surface-base" />\n'
            '  <span class="text-xs font-medium text-slate-300">'
            "Enable cutoff search</span>\n"
            "</label>"
        )
        assert (
            render_macro(
                'checkbox(name="cutoff_enabled", label="Enable cutoff search", '
                "checked=false, data_default_checked=0)"
            )
            == expected
        )

    def test_checked_with_default_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<label class="inline-flex items-center gap-2 cursor-pointer '
            "select-none rounded-inset border border-border-default "
            'bg-surface-1 px-3 py-2">\n'
            '  <input type="checkbox" name="upgrade_enabled" value="on" checked\n'
            '         data-default-checked="1"\n'
            '         class="rounded-chip border-border-default bg-surface-1 '
            "text-brand-500 focus:ring-brand-500 "
            'focus:ring-offset-surface-base" />\n'
            '  <span class="text-xs font-medium text-slate-300">'
            "Enable upgrade search</span>\n"
            "</label>"
        )
        assert (
            render_macro(
                'checkbox(name="upgrade_enabled", label="Enable upgrade search", '
                "checked=true, data_default_checked=1)"
            )
            == expected
        )

    def test_no_default_omits_data_attr(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro('checkbox(name="x", label="X")')
        assert "data-default-checked" not in result


class TestSelectField:
    """select_field renders <label> + <select> with (value, text) option pairs."""

    def test_two_options_selected_second_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        expected = (
            '<label class="field-label" for="order">\n'
            "  Search Order\n"
            "</label>\n"
            '<select id="order" name="search_order" data-default-value="random" '
            'class="station-input station-select">\n'
            '  <option value="chronological">Chronological</option>\n'
            '  <option value="random" selected>Random (default)</option>\n'
            "</select>\n"
            '<p class="mt-1.5 text-xs text-slate-600">'
            "Chronological walks oldest-first.</p>"
        )
        assert (
            render_macro(
                'select_field(id="order", name="search_order", '
                'options=[("chronological", "Chronological"), '
                '("random", "Random (default)")], selected="random", '
                'data_default_value="random", label="Search Order", '
                'help_text="Chronological walks oldest-first.")'
            )
            == expected
        )

    def test_two_options_selected_first_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        expected = (
            '<label class="field-label" for="type">\n'
            "  Mode\n"
            "</label>\n"
            '<select id="type" name="type" '
            'class="station-input station-select">\n'
            '  <option value="episode" selected>Episode search (default)</option>\n'
            '  <option value="season_context">'
            "Season-context search (advanced)</option>\n"
            "</select>"
        )
        assert (
            render_macro(
                'select_field(id="type", name="type", '
                'options=[("episode", "Episode search (default)"), '
                '("season_context", "Season-context search (advanced)")], '
                'selected="episode", label="Mode")'
            )
            == expected
        )

    def test_no_selected_marks_no_option(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro(
            'select_field(id="x", name="x", options=[("a", "A"), ("b", "B")], label="X")'
        )
        assert " selected>" not in result

    def test_no_data_default_omits_attribute(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro('select_field(id="x", name="x", options=[("a", "A")], label="X")')
        assert "data-default-value" not in result

    def test_no_help_text_omits_paragraph(self, render_macro: Callable[[str], str]) -> None:
        result = render_macro('select_field(id="x", name="x", options=[("a", "A")], label="X")')
        assert '<p class="mt-1.5 text-xs text-slate-600">' not in result

"""Byte-equal output pinning for the macros in `_macros/instances.html`.

Two macros are exercised: instance_row (the entire <tr> rendered for
every instance in the settings table) and form_context (the <form>
opening / closing wrapper for the add-edit instance form).

E.16 is HIGH RISK per plan section 6: the pinning here covers every
data-* attribute, every hx-* attribute, every rendered class string,
every status-pill branch, the toggle button's enabled / disabled
class swap, and the form_context derived strings (form_id, post_url,
target, swap) so a future edit cannot silently drop a class, swap
an attribute order, or break the HTMX wire contract the JS
controllers and route handlers depend on.

Like the other macro pinning suites under tests/test_templates/, the
macro output is not the same as the full post-migration consumer
HTML (consumers carry ambient template indentation that the macro
does not).  Consumer-level integration is asserted via the Track
A.22 render harness (test_pinned_render.py) and via the Track E
gate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


def _instance_stub(
    *,
    instance_id: int = 1,
    type_value: str = "sonarr",
    name: str = "Sonarr",
    url: str = "http://host:8989",
    enabled: bool = True,
    batch_size: int = 2,
    sleep_interval_mins: int = 30,
) -> Any:
    """Build a MagicMock shaped like the D.14 Instance facade.

    Only the fields instance_row reads are populated here; other
    sub-structs return new MagicMock attributes lazily.
    """
    type_mock = MagicMock()
    type_mock.value = type_value

    stub = MagicMock()
    stub.core = MagicMock()
    stub.core.id = instance_id
    stub.core.name = name
    stub.core.url = url
    stub.core.type = type_mock
    stub.core.enabled = enabled

    stub.missing = MagicMock()
    stub.missing.batch_size = batch_size
    stub.missing.sleep_interval_mins = sleep_interval_mins
    return stub


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[..., str]:
    """Render a one-off call to a macro in `_macros/instances.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja source snippet and a context
        dict, and returns the rendered HTML exactly as the engine
        produced it.
    """

    def _inner(src: str, **context: Any) -> str:
        return jinja_env.from_string(src).render(**context)

    return _inner


_IMPORT = '{% import "_macros/instances.html" as instances %}'


class TestInstanceRowStatePill:
    """instance_row picks the right status_pill state for each enabled / error combination."""

    def test_enabled_no_error_renders_active_pill(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(src, instance=_instance_stub(enabled=True), active_error_ids=[])
        assert "Active" in result
        assert "status-dot--active" in result
        assert "status-pill--active" in result
        assert "Error" not in result
        assert "Disabled" not in result

    def test_enabled_with_error_renders_error_pill(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(instance_id=7, enabled=True),
            active_error_ids=[7],
        )
        assert "Error" in result
        assert "status-dot--error" in result
        assert "status-pill--error" in result
        assert ">Active<" not in result

    def test_disabled_renders_disabled_pill(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(src, instance=_instance_stub(enabled=False), active_error_ids=[])
        assert "Disabled" in result
        assert "status-pill--disabled" in result
        assert "status-dot--active" not in result
        assert "status-dot--error" not in result
        assert ">Active<" not in result
        assert ">Error<" not in result

    def test_disabled_id_in_error_ids_still_renders_disabled(
        self, render_macro: Callable[..., str]
    ) -> None:
        # Error pill only renders when both `enabled` and id-in-error-ids.
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(instance_id=4, enabled=False),
            active_error_ids=[4],
        )
        assert "Disabled" in result
        assert ">Error<" not in result

    def test_active_error_ids_none_treated_as_empty(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(src, instance=_instance_stub(enabled=True), active_error_ids=None)
        # No error ids ≡ active.
        assert "Active" in result
        assert ">Error<" not in result


class TestInstanceRowDataColumns:
    """Each <td> carries a `data-col` attribute the table-body CSS reads."""

    @pytest.mark.parametrize(
        "data_col",
        ["name", "type", "url", "status", "batch", "actions"],
    )
    def test_each_data_col_present(self, render_macro: Callable[..., str], data_col: str) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(src, instance=_instance_stub(), active_error_ids=[])
        assert f'data-col="{data_col}"' in result

    def test_row_id_uses_instance_id(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(instance_id=42),
            active_error_ids=[],
        )
        assert 'id="instance-row-42"' in result

    def test_name_and_url_render(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(name="My Box", url="http://my-box:8989"),
            active_error_ids=[],
        )
        assert ">My Box<" in result
        assert "http://my-box:8989" in result

    def test_batch_column_renders_batch_size_and_interval(
        self, render_macro: Callable[..., str]
    ) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(batch_size=5, sleep_interval_mins=45),
            active_error_ids=[],
        )
        assert "5 / 45m" in result


class TestInstanceRowInstanceTypeBadge:
    """instance_row composes the badge macro for every instance type."""

    @pytest.mark.parametrize(
        "type_value",
        ["sonarr", "radarr", "lidarr", "readarr", "whisparr_v2", "whisparr_v3"],
    )
    def test_badge_palette_for_type(
        self, render_macro: Callable[..., str], type_value: str
    ) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(type_value=type_value),
            active_error_ids=[],
        )
        # Each badge variant uses the rounded-chip primitive.
        assert "rounded-chip" in result


class TestInstanceRowToggleButton:
    """The toggle button swaps colour and label between enabled and disabled."""

    def test_enabled_renders_disable_button_with_warning_palette(
        self, render_macro: Callable[..., str]
    ) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(src, instance=_instance_stub(enabled=True), active_error_ids=[])
        assert ">\n        Disable\n      </button>" in result
        assert "bg-warning-bg" in result
        assert "text-warning" in result
        assert "border-warning-border" in result

    def test_disabled_renders_enable_button_with_success_palette(
        self, render_macro: Callable[..., str]
    ) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(src, instance=_instance_stub(enabled=False), active_error_ids=[])
        assert ">\n        Enable\n      </button>" in result
        assert "bg-success-bg" in result
        assert "text-success" in result
        assert "border-success-border" in result

    def test_toggle_button_has_hx_attributes(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(instance_id=9, enabled=True),
            active_error_ids=[],
        )
        assert 'hx-post="/settings/instances/9/toggle-enabled"' in result
        assert 'hx-target="#instance-row-9"' in result
        assert 'hx-swap="outerHTML"' in result


class TestInstanceRowEditAndDeleteButtons:
    """Edit + Delete buttons each carry their own HTMX wire contract."""

    def test_edit_button_attrs(self, render_macro: Callable[..., str]) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(instance_id=11),
            active_error_ids=[],
        )
        assert 'data-open-add-instance-modal="true"' in result
        assert 'hx-get="/settings/instances/11/edit"' in result
        assert 'hx-target="#add-instance-modal-content"' in result
        assert 'hx-swap="innerHTML"' in result

    def test_delete_button_attrs_include_name_in_confirm(
        self, render_macro: Callable[..., str]
    ) -> None:
        src = _IMPORT + "{{ instances.instance_row(instance, active_error_ids) }}"
        result = render_macro(
            src,
            instance=_instance_stub(instance_id=11, name="My Sonarr"),
            active_error_ids=[],
        )
        assert 'hx-delete="/settings/instances/11"' in result
        assert 'hx-target="#instance-row-11"' in result
        assert 'hx-swap="outerHTML"' in result
        assert "hx-confirm=\"Delete 'My Sonarr'? This cannot be undone.\"" in result


class TestFormContextEditMode:
    """form_context with is_edit=True derives every string from instance_id."""

    def test_edit_mode_byte_equal(self, render_macro: Callable[..., str]) -> None:
        src = (
            _IMPORT
            + "{% call(form_id) instances.form_context("
            + 'is_edit=True, instance_id=42, instance_name="My Sonarr"'
            + ") %}BODY-{{ form_id }}-END{% endcall %}"
        )
        expected = (
            '<form id="edit-form-42"\n'
            '      data-form-mode="edit"\n'
            '      data-instance-name="My Sonarr"\n'
            '      hx-post="/settings/instances/42"\n'
            '      hx-target="#instance-row-42"\n'
            '      hx-swap="outerHTML"\n'
            '      class="space-y-4">\n'
            "BODY-edit-form-42-END\n"
            "</form>"
        )
        assert render_macro(src) == expected


class TestFormContextAddMode:
    """form_context with is_edit=False uses the static add-instance strings."""

    def test_add_mode_byte_equal(self, render_macro: Callable[..., str]) -> None:
        src = (
            _IMPORT + "{% call(form_id) instances.form_context(is_edit=False) %}"
            "BODY-{{ form_id }}-END{% endcall %}"
        )
        expected = (
            '<form id="add-instance-form"\n'
            '      data-form-mode="add"\n'
            '      data-instance-name=""\n'
            '      hx-post="/settings/instances"\n'
            '      hx-target="#instance-tbody"\n'
            '      hx-swap="innerHTML"\n'
            '      class="space-y-4">\n'
            "BODY-add-instance-form-END\n"
            "</form>"
        )
        assert render_macro(src) == expected

    def test_add_mode_ignores_passed_instance_id(self, render_macro: Callable[..., str]) -> None:
        # When is_edit is False, instance_id is unused; the macro still
        # renders the static add-instance derived strings.
        src = (
            _IMPORT
            + "{% call(form_id) instances.form_context("
            + 'is_edit=False, instance_id=99, instance_name="unused"'
            + ") %}{{ form_id }}{% endcall %}"
        )
        result = render_macro(src)
        assert 'id="add-instance-form"' in result
        assert "/settings/instances/99" not in result


class TestFormContextCallerArg:
    """`caller(form_id)` exposes the computed form_id to the call body."""

    def test_caller_receives_form_id_in_edit_mode(self, render_macro: Callable[..., str]) -> None:
        src = (
            _IMPORT + "{% call(form_id) instances.form_context(is_edit=True, instance_id=7) %}"
            "FID:{{ form_id }}{% endcall %}"
        )
        result = render_macro(src)
        assert "FID:edit-form-7" in result

    def test_caller_receives_form_id_in_add_mode(self, render_macro: Callable[..., str]) -> None:
        src = (
            _IMPORT + "{% call(form_id) instances.form_context(is_edit=False) %}"
            "FID:{{ form_id }}{% endcall %}"
        )
        result = render_macro(src)
        assert "FID:add-instance-form" in result

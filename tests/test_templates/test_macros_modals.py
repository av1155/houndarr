"""Byte-equal output pinning for the macros in `_macros/modals.html`.

Two macros are exercised: dialog_shell (the native <dialog> wrapper
shared by the changelog modal and the add-instance modal) and
confirm_dialog_shell (the BEM .confirm-dialog backdrop + panel pair
used by the shared admin confirm prompt).

Each test invokes the macro in isolation and asserts the exact
bytes Jinja emits.  Consumer-level integration (the full rendered
HTML including ambient template indentation) is asserted via the
render harness in ``test_pinned_render.py`` and the macro
inventory gate.  What this suite locks down is every attribute,
every conditional emission, and the structure the JS controllers
read so a future edit cannot silently drop a wrapper class, swap
an aria attribute, or reorder the backdrop / panel divs.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/modals.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja source snippet (the caller is
        responsible for the `{% import %}` and `{% call %}` boilerplate
        because every modal is rendered via a call-block body) and
        returns the rendered HTML exactly as the engine produced it.
    """

    def _inner(src: str) -> str:
        return jinja_env.from_string(src).render()

    return _inner


_IMPORT = '{% import "_macros/modals.html" as modals %}'


class TestDialogShellAriaCombinations:
    """dialog_shell renders aria-labelledby and aria-describedby conditionally."""

    def test_no_aria_attrs_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + '{% call modals.dialog_shell(id="m", width="40rem") %}body{% endcall %}'
        expected = (
            "<dialog\n"
            '  id="m"\n'
            '  class="m-auto p-0 w-[min(94vw,40rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            ">\n"
            "body\n"
            "</dialog>"
        )
        assert render_macro(src) == expected

    def test_aria_labelledby_only_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="m", width="40rem", '
            'aria_labelledby="title") %}body{% endcall %}'
        )
        expected = (
            "<dialog\n"
            '  id="m"\n'
            '  class="m-auto p-0 w-[min(94vw,40rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            '  aria-labelledby="title"\n'
            ">\n"
            "body\n"
            "</dialog>"
        )
        assert render_macro(src) == expected

    def test_both_aria_attrs_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="m", width="40rem", '
            'aria_labelledby="title", aria_describedby="sub") %}body{% endcall %}'
        )
        expected = (
            "<dialog\n"
            '  id="m"\n'
            '  class="m-auto p-0 w-[min(94vw,40rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            '  aria-labelledby="title"\n'
            '  aria-describedby="sub"\n'
            ">\n"
            "body\n"
            "</dialog>"
        )
        assert render_macro(src) == expected

    def test_describedby_without_labelledby_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="m", width="40rem", '
            'aria_describedby="sub") %}body{% endcall %}'
        )
        expected = (
            "<dialog\n"
            '  id="m"\n'
            '  class="m-auto p-0 w-[min(94vw,40rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            '  aria-describedby="sub"\n'
            ">\n"
            "body\n"
            "</dialog>"
        )
        assert render_macro(src) == expected


class TestDialogShellWidth:
    """The `width` parameter is interpolated into w-[min(94vw,WIDTH)]."""

    @pytest.mark.parametrize("width", ["38rem", "52rem", "60rem"])
    def test_width_interpolated_into_class(
        self, render_macro: Callable[[str], str], width: str
    ) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="m", width="' + width + '") %}b{% endcall %}'
        )
        result = render_macro(src)
        assert f"w-[min(94vw,{width})]" in result


class TestDialogShellConsumerCallSites:
    """Each migrated dialog renders byte-equal to its hand-built post-refactor form."""

    def test_changelog_modal_with_subtitle_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="changelog-modal", width="38rem", '
            'aria_labelledby="changelog-modal-title", '
            'aria_describedby="changelog-modal-subtitle") %}BODY{% endcall %}'
        )
        expected = (
            "<dialog\n"
            '  id="changelog-modal"\n'
            '  class="m-auto p-0 w-[min(94vw,38rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            '  aria-labelledby="changelog-modal-title"\n'
            '  aria-describedby="changelog-modal-subtitle"\n'
            ">\n"
            "BODY\n"
            "</dialog>"
        )
        assert render_macro(src) == expected

    def test_changelog_modal_without_subtitle_byte_equal(
        self, render_macro: Callable[[str], str]
    ) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="changelog-modal", width="38rem", '
            'aria_labelledby="changelog-modal-title") %}BODY{% endcall %}'
        )
        expected = (
            "<dialog\n"
            '  id="changelog-modal"\n'
            '  class="m-auto p-0 w-[min(94vw,38rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            '  aria-labelledby="changelog-modal-title"\n'
            ">\n"
            "BODY\n"
            "</dialog>"
        )
        assert render_macro(src) == expected

    def test_add_instance_modal_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call modals.dialog_shell(id="add-instance-modal", '
            'width="52rem") %}BODY{% endcall %}'
        )
        expected = (
            "<dialog\n"
            '  id="add-instance-modal"\n'
            '  class="m-auto p-0 w-[min(94vw,52rem)] rounded-container border '
            'border-border-subtle bg-surface-1 text-slate-100 shadow-modal"\n'
            ">\n"
            "BODY\n"
            "</dialog>"
        )
        assert render_macro(src) == expected


class TestConfirmDialogShell:
    """confirm_dialog_shell wraps a backdrop + panel around the call body."""

    def test_default_args_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + "{% call modals.confirm_dialog_shell() %}FORM{% endcall %}"
        expected = (
            "<div\n"
            '  id="confirm-dialog"\n'
            '  class="confirm-dialog hidden"\n'
            '  role="dialog"\n'
            '  aria-modal="true"\n'
            '  aria-labelledby="confirm-title"\n'
            ">\n"
            '  <div class="confirm-dialog__backdrop" data-dismiss-confirm></div>\n'
            '  <div class="confirm-dialog__panel rounded-container border '
            'border-border-default bg-surface-1">\n'
            "FORM\n"
            "  </div>\n"
            "</div>"
        )
        assert render_macro(src) == expected

    def test_custom_id_and_aria_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        src = (
            _IMPORT + '{% call modals.confirm_dialog_shell(id="other-confirm", '
            'aria_labelledby="other-title") %}X{% endcall %}'
        )
        expected = (
            "<div\n"
            '  id="other-confirm"\n'
            '  class="confirm-dialog hidden"\n'
            '  role="dialog"\n'
            '  aria-modal="true"\n'
            '  aria-labelledby="other-title"\n'
            ">\n"
            '  <div class="confirm-dialog__backdrop" data-dismiss-confirm></div>\n'
            '  <div class="confirm-dialog__panel rounded-container border '
            'border-border-default bg-surface-1">\n'
            "X\n"
            "  </div>\n"
            "</div>"
        )
        assert render_macro(src) == expected

    def test_backdrop_precedes_panel(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + "{% call modals.confirm_dialog_shell() %}body{% endcall %}"
        result = render_macro(src)
        backdrop_at = result.index("confirm-dialog__backdrop")
        panel_at = result.index("confirm-dialog__panel")
        assert backdrop_at < panel_at

    def test_caller_body_lands_inside_panel(self, render_macro: Callable[[str], str]) -> None:
        src = _IMPORT + "{% call modals.confirm_dialog_shell() %}MARKER{% endcall %}"
        result = render_macro(src)
        panel_open = result.index('class="confirm-dialog__panel')
        marker = result.index("MARKER")
        panel_close_at = result.index("</div>\n</div>")
        assert panel_open < marker < panel_close_at

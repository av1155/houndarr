"""Byte-equal render pinning for `_macros/badges.html`.

E.2 through E.4 migrate consumer templates to these macros.  Every
class string, label, and branch label emitted here is asserted
verbatim so the migration diffs cannot silently drop a utility
class, reorder attributes, or rename a label that CSS or the HTMX
client depends on.

Each assertion targets the exact byte sequence Jinja produces when
the macro is invoked with a given value, including whitespace-free
concatenation of adjacent spans in the ``status_pill`` output.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from jinja2 import Environment

pytestmark = pytest.mark.pinning


@pytest.fixture()
def render_macro(jinja_env: Environment) -> Callable[[str], str]:
    """Render a one-off call to a macro in `_macros/badges.html`.

    Args:
        jinja_env: the shared environment from tests/test_templates/conftest.py.

    Returns:
        A callable that accepts a Jinja expression importing and
        invoking a macro (e.g. ``"instance_type_badge('sonarr')"``)
        and returns the rendered HTML exactly as the engine produced it.
    """

    def _inner(call_expr: str) -> str:
        src = "{% import '_macros/badges.html' as badges %}{{ badges." + call_expr + " }}"
        return jinja_env.from_string(src).render()

    return _inner


class TestInstanceTypeBadge:
    @pytest.mark.parametrize(
        ("type_value", "label", "palette"),
        [
            ("sonarr", "Sonarr", ("bg-sonarr-bg", "text-sonarr", "border-sonarr/40")),
            ("radarr", "Radarr", ("bg-radarr-bg", "text-radarr", "border-radarr/40")),
            ("lidarr", "Lidarr", ("bg-lidarr-bg", "text-lidarr", "border-lidarr/40")),
            ("readarr", "Readarr", ("bg-readarr-bg", "text-readarr", "border-readarr/40")),
            (
                "whisparr_v2",
                "Whisparr v2",
                ("bg-whisparr-v2-bg", "text-whisparr-v2", "border-whisparr-v2/40"),
            ),
            (
                "whisparr_v3",
                "Whisparr v3",
                ("bg-whisparr-v3-bg", "text-whisparr-v3", "border-whisparr-v3/40"),
            ),
        ],
    )
    def test_known_type_byte_equal(
        self,
        render_macro: Callable[[str], str],
        type_value: str,
        label: str,
        palette: tuple[str, str, str],
    ) -> None:
        bg, text, border = palette
        expected = (
            '<span class="inline-flex items-center px-2 py-0.5 rounded-chip '
            f'text-xs font-mono {bg} {text} border {border}">{label}</span>'
        )
        assert render_macro(f"instance_type_badge({type_value!r})") == expected

    def test_unknown_type_falls_back_to_sonarr(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<span class="inline-flex items-center px-2 py-0.5 rounded-chip '
            'text-xs font-mono bg-sonarr-bg text-sonarr border border-sonarr/40">'
            "Sonarr</span>"
        )
        assert render_macro("instance_type_badge('definitely_not_a_real_type')") == expected


class TestLogActionBadge:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            ("searched", '<span class="badge badge-soft badge-success">searched</span>'),
            ("skipped", '<span class="badge badge-soft badge-warning">skipped</span>'),
            ("error", '<span class="badge badge-soft badge-error">error</span>'),
            ("info", '<span class="badge badge-soft badge-neutral">info</span>'),
        ],
    )
    def test_each_action_byte_equal(
        self,
        render_macro: Callable[[str], str],
        action: str,
        expected: str,
    ) -> None:
        assert render_macro(f"log_action_badge({action!r})") == expected

    def test_unknown_action_renders_info_fallback(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="badge badge-soft badge-neutral">info</span>'
        assert render_macro("log_action_badge('unmapped')") == expected

    def test_none_action_renders_info_fallback(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="badge badge-soft badge-neutral">info</span>'
        assert render_macro("log_action_badge(None)") == expected


class TestLogKindBadge:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("missing", '<span class="badge badge-soft badge-info">missing</span>'),
            ("cutoff", '<span class="badge badge-soft badge-warning">cutoff</span>'),
            (
                "upgrade",
                (
                    '<span class="badge badge-soft text-seg-upgrade-cd '
                    'border-seg-upgrade-cd bg-seg-upgrade-cd/15">upgrade</span>'
                ),
            ),
        ],
    )
    def test_known_kind_byte_equal(
        self,
        render_macro: Callable[[str], str],
        kind: str,
        expected: str,
    ) -> None:
        assert render_macro(f"log_kind_badge({kind!r})") == expected

    def test_unknown_kind_renders_dash(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="text-slate-600">-</span>'
        assert render_macro("log_kind_badge('unmapped')") == expected

    def test_none_kind_renders_dash(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="text-slate-600">-</span>'
        assert render_macro("log_kind_badge(None)") == expected


class TestLogTriggerBadge:
    @pytest.mark.parametrize(
        ("trigger", "expected"),
        [
            ("scheduled", '<span class="badge badge-soft badge-primary">scheduled</span>'),
            ("run_now", '<span class="badge badge-soft badge-success">run_now</span>'),
            ("system", '<span class="badge badge-soft badge-neutral">system</span>'),
        ],
    )
    def test_known_trigger_byte_equal(
        self,
        render_macro: Callable[[str], str],
        trigger: str,
        expected: str,
    ) -> None:
        assert render_macro(f"log_trigger_badge({trigger!r})") == expected

    def test_unknown_trigger_renders_dash(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="text-slate-600">-</span>'
        assert render_macro("log_trigger_badge('unmapped')") == expected

    def test_none_trigger_renders_dash(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="text-slate-600">-</span>'
        assert render_macro("log_trigger_badge(None)") == expected


class TestLogCycleOutcomeBadge:
    def test_progress_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="badge badge-soft badge-success">searched</span>'
        assert render_macro("log_cycle_outcome_badge('progress')") == expected

    def test_no_progress_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="badge badge-soft badge-neutral">skips only</span>'
        assert render_macro("log_cycle_outcome_badge('no_progress')") == expected

    def test_unknown_outcome_renders_unknown_span(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="text-[0.625rem] text-slate-600 font-mono">unknown</span>'
        assert render_macro("log_cycle_outcome_badge('partial')") == expected

    def test_none_outcome_renders_unknown_span(self, render_macro: Callable[[str], str]) -> None:
        expected = '<span class="text-[0.625rem] text-slate-600 font-mono">unknown</span>'
        assert render_macro("log_cycle_outcome_badge(None)") == expected


class TestStatusPill:
    def test_active_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<span class="status-pill status-pill--active">'
            '<span class="status-dot status-dot--active" title="Search enabled" '
            'aria-label="Search enabled"></span>'
            '<span class="hidden sm:inline">Active</span>'
            "</span>"
        )
        assert render_macro("status_pill('active')") == expected

    def test_error_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<span class="status-pill status-pill--error">'
            '<span class="status-dot status-dot--error" title="Instance is reporting errors" '
            'aria-label="Instance is reporting errors"></span>'
            '<span class="hidden sm:inline">Error</span>'
            "</span>"
        )
        assert render_macro("status_pill('error')") == expected

    def test_disabled_byte_equal(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<span class="status-pill status-pill--disabled">'
            '<span class="status-dot status-dot--disabled" title="Search disabled" '
            'aria-label="Search disabled"></span>'
            '<span class="hidden sm:inline">Disabled</span>'
            "</span>"
        )
        assert render_macro("status_pill('disabled')") == expected

    def test_unknown_state_falls_back_to_disabled(self, render_macro: Callable[[str], str]) -> None:
        expected = (
            '<span class="status-pill status-pill--disabled">'
            '<span class="status-dot status-dot--disabled" title="Search disabled" '
            'aria-label="Search disabled"></span>'
            '<span class="hidden sm:inline">Disabled</span>'
            "</span>"
        )
        assert render_macro("status_pill('not_a_state')") == expected

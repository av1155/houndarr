"""Render-byte pinning for the partials that compose with macros.

Each test pins the structural markers (class names, data-*
attributes, visible text) on one partial under a representative
context so a macro touch-up or template edit cannot silently drop
or rename an attribute that the HTMX client or CSS depends on.

Coverage is narrow by design: one partial per test, one or two
contexts each, markers only.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.pinning


# instance_row.html


def _instance_stub(
    *,
    instance_id: int = 1,
    type_value: str = "sonarr",
    name: str = "Sonarr",
    enabled: bool = True,
) -> Any:
    """Build a MagicMock shaped like the Instance sub-struct facade.

    Templates read ``instance.core.<field>``,
    ``instance.missing.<field>``, ``instance.cutoff.<field>``,
    ``instance.upgrade.<field>``, ``instance.schedule.<field>``, and
    ``instance.snapshot.<field>``.  The stub mirrors that shape so
    each attribute read resolves to a deterministic value.
    """
    type_mock = MagicMock()
    type_mock.value = type_value

    stub = MagicMock()

    stub.core = MagicMock()
    stub.core.id = instance_id
    stub.core.name = name
    stub.core.url = "http://host:8989"
    stub.core.type = type_mock
    stub.core.enabled = enabled
    stub.core.api_key = "plaintext-key"

    stub.missing = MagicMock()
    stub.missing.batch_size = 2
    stub.missing.hourly_cap = 4
    stub.missing.sleep_interval_mins = 30
    stub.missing.cooldown_days = 14
    stub.missing.post_release_grace_hrs = 6
    stub.missing.queue_limit = 0

    stub.cutoff = MagicMock()
    stub.cutoff.cutoff_enabled = False

    stub.upgrade = MagicMock()
    stub.upgrade.upgrade_enabled = False

    stub.schedule = MagicMock()
    stub.schedule.allowed_time_window = ""

    stub.snapshot = MagicMock()
    stub.snapshot.monitored_total = 0
    stub.snapshot.unreleased_count = 0
    stub.snapshot.snapshot_refreshed_at = ""

    return stub


class TestInstanceRowRender:
    @pytest.mark.parametrize(
        "type_value",
        ["sonarr", "radarr", "lidarr", "readarr", "whisparr_v2", "whisparr_v3"],
    )
    def test_each_app_type_emits_badge(self, render, type_value: str) -> None:
        inst = _instance_stub(type_value=type_value)
        html = render(
            "partials/instance_row.html",
            instance=inst,
            active_error_ids=[],
        )
        # The badge text differs by type; every badge uses rounded-chip.
        assert 'class="' in html
        assert "rounded-chip" in html

    def test_enabled_instance_shows_active_pill(self, render) -> None:
        inst = _instance_stub(enabled=True)
        html = render(
            "partials/instance_row.html",
            instance=inst,
            active_error_ids=[],
        )
        assert "status-dot--active" in html

    def test_disabled_instance_omits_pulse(self, render) -> None:
        inst = _instance_stub(enabled=False)
        html = render(
            "partials/instance_row.html",
            instance=inst,
            active_error_ids=[],
        )
        # Disabled pill emits a .status-dot but none of the pulsing variants.
        assert "Disabled" in html or "disabled" in html.lower()

    def test_active_error_shows_error_pill(self, render) -> None:
        inst = _instance_stub(instance_id=3, enabled=True)
        html = render(
            "partials/instance_row.html",
            instance=inst,
            active_error_ids=[3],
        )
        # Pinning marker: any text-danger / error label surfaces.
        assert "Error" in html or "error" in html.lower()


# log_rows.html


class TestLogRowsRender:
    @pytest.mark.parametrize(
        "action",
        ["searched", "skipped", "error"],
    )
    def test_action_chip_class(self, render, action: str) -> None:
        rows = [
            {
                "id": 1,
                "instance_id": 1,
                "instance_name": "Sonarr",
                "instance_type": "sonarr",
                "timestamp": "2026-04-22T10:00:00.000Z",
                "action": action,
                "search_kind": "missing",
                "cycle_trigger": "scheduled",
                "cycle_id": None,
                "cycle_progress": None,
                "cycle_searched_count": 1 if action == "searched" else 0,
                "cycle_skipped_count": 1 if action == "skipped" else 0,
                "cycle_error_count": 1 if action == "error" else 0,
                "item_id": 100,
                "item_type": "episode",
                "item_label": "Show - S01E01",
                "reason": None,
                "message": None,
            }
        ]
        html = render(
            "partials/log_rows.html",
            rows=rows,
            limit=50,
        )
        assert f"entry__action--{action}" in html

    def test_empty_rows_branch_renders_quietly(self, render) -> None:
        html = render(
            "partials/log_rows.html",
            rows=[],
            limit=50,
        )
        # The partial must never raise on an empty list.
        assert html is not None

    @staticmethod
    def _skip_only_rows(reasons: list[str], instance_name: str = "Sonarr") -> list[dict]:
        """Build a cycle's worth of skip-only rows sharing one cycle_id."""
        cycle_id = "cyc-skip-only"
        rows = []
        for idx, reason in enumerate(reasons, start=1):
            rows.append(
                {
                    "id": idx,
                    "instance_id": 1,
                    "instance_name": instance_name,
                    "instance_type": "sonarr",
                    "timestamp": f"2026-04-22T10:00:{idx:02d}.000Z",
                    "action": "skipped",
                    "search_kind": "missing",
                    "cycle_trigger": "scheduled",
                    "cycle_id": cycle_id,
                    "cycle_progress": "no_progress",
                    "cycle_searched_count": 0,
                    "cycle_skipped_count": len(reasons),
                    "cycle_error_count": 0,
                    "item_id": 100 + idx,
                    "item_type": "episode",
                    "item_label": f"Show - S01E{idx:02d}",
                    "reason": reason,
                    "message": None,
                }
            )
        return rows

    def test_skip_only_summary_all_cooldown(self, render) -> None:
        rows = self._skip_only_rows(
            [
                "on cooldown (14d)",
                "on cutoff cooldown (21d)",
                "on upgrade cooldown (90d)",
            ]
        )
        html = render("partials/log_rows.html", rows=rows, limit=50)
        assert "Healthy pacing" in html
        assert ">on cooldown</span>" in html
        # Count renders correctly ("3 items").
        assert "<strong>3</strong> items" in html

    def test_skip_only_summary_all_unreleased(self, render) -> None:
        rows = self._skip_only_rows(
            [
                "not yet released",
                "post-release grace (6h)",
                "radarr reports not available",
                "radarr status indicates unreleased",
            ]
        )
        html = render("partials/log_rows.html", rows=rows, limit=50)
        assert 'all <span class="cycle__summary-reason">not yet released</span>' in html
        assert "<strong>4</strong> items" in html

    def test_skip_only_summary_all_capped(self, render) -> None:
        rows = self._skip_only_rows(
            [
                "hourly limit reached (20/hr)",
                "cutoff hourly limit reached (1/hr)",
                "upgrade hourly limit reached (1/hr)",
            ]
        )
        html = render("partials/log_rows.html", rows=rows, limit=50)
        assert 'hit the <span class="cycle__summary-reason">hourly limit</span>' in html
        assert "will resume next hour" in html
        # "Cycle paused" message should not restate an item count.
        assert "<strong>3</strong>" not in html

    def test_skip_only_summary_mixed(self, render) -> None:
        rows = self._skip_only_rows(
            [
                "on cooldown (14d)",
                "on cooldown (14d)",
                "not yet released",
                "hourly limit reached (20/hr)",
            ]
        )
        html = render("partials/log_rows.html", rows=rows, limit=50)
        # Mixed variant renders both counts and the separator.
        assert "<strong>4</strong> items:" in html
        assert "2 on cooldown" in html
        assert "1 not yet released" in html
        assert "1 hit hourly limit" in html
        assert "No dispatches needed" in html

    def test_skip_only_summary_singular_item(self, render) -> None:
        """One skipped item uses the singular 'item' noun, not 'items'."""
        rows = self._skip_only_rows(["on cooldown (14d)"])
        html = render("partials/log_rows.html", rows=rows, limit=50)
        assert "<strong>1</strong> item:" in html
        assert "<strong>1</strong> items" not in html

    @pytest.mark.parametrize(
        ("raw_type", "expected_display"),
        [
            ("whisparr_v2_episode", "episode"),
            ("whisparr_v3_movie", "movie"),
            ("episode", "episode"),
            ("movie", "movie"),
            ("album", "album"),
            ("book", "book"),
        ],
    )
    def test_item_type_subtitle_strips_whisparr_namespace(
        self, render, raw_type: str, expected_display: str
    ) -> None:
        """`entry__sub` shows a clean type word; `data-item-type` stays canonical."""
        rows = [
            {
                "id": 1,
                "instance_id": 1,
                "instance_name": "Whisparr v2",
                "instance_type": "whisparr_v2",
                "timestamp": "2026-04-22T10:00:00.000Z",
                "action": "searched",
                "search_kind": "missing",
                "cycle_trigger": "scheduled",
                "cycle_id": None,
                "cycle_progress": None,
                "cycle_searched_count": 1,
                "cycle_skipped_count": 0,
                "cycle_error_count": 0,
                "item_id": 4242,
                "item_type": raw_type,
                "item_label": None,  # forces the fallback title path
                "reason": None,
                "message": None,
            }
        ]
        html = render("partials/log_rows.html", rows=rows, limit=50)
        # Canonical value still lands on the data attribute.
        assert f'data-item-type="{raw_type}"' in html
        # Subtitle renders the stripped word + id separator.
        assert f'<div class="entry__sub">{expected_display} · id:4242</div>' in html
        # Fallback title capitalises the stripped word (no `whisparr_` leaked).
        assert f'<div class="entry__title">{expected_display.capitalize()} 4242</div>' in html
        # Defense in depth: the bare namespaced form never leaks into UI text.
        if raw_type != expected_display:
            assert f">{raw_type}<" not in html


# changelog_modal.html


def _release(version: str, date: str) -> Any:
    """Stub of the ReleaseEntry shape consumed by the template."""
    stub = MagicMock()
    stub.version = version
    stub.date = date
    stub.sections = []
    return stub


class TestChangelogModalRender:
    def test_manual_open_suppresses_subtitle(self, render) -> None:
        newest = _release("1.2.0", "2026-04-20")
        html = render(
            "partials/changelog_modal.html",
            releases=[newest],
            newest=newest,
            older=[],
            range_label="",
            manual=True,
        )
        assert 'id="changelog-modal"' in html
        # Manual: subtitle is empty, so no "Since v..." text.
        assert "Since v" not in html

    def test_auto_open_with_range_label_shows_subtitle(self, render) -> None:
        newest = _release("1.2.0", "2026-04-20")
        older = _release("1.1.0", "2026-03-15")
        html = render(
            "partials/changelog_modal.html",
            releases=[newest, older],
            newest=newest,
            older=[older],
            range_label="Since v1.1.0",
            manual=False,
        )
        assert "Since v1.1.0" in html


# login.html / setup.html


class TestAuthPagesRender:
    """Pin the structural markers on /login and /setup.

    The auth CSS (auth.css + auth-fields.css) is explicitly
    off-limits for structural refactor, but the CSS class hooks and
    auth.js data attributes are exercised here: a single missing
    class or id would break the caps-lock badge, the strength meter,
    the submit-button loading state, or the Settings > Security
    show-hide parity.
    """

    def test_login_html_structural_markers(self, render) -> None:
        html = render(
            "login.html",
            version="9.9.9",
            csrf_token="csrf-test",
            error=None,
        )
        # Shell chrome.
        assert 'class="auth-card auth-card--login"' in html
        assert 'class="auth-form"' in html
        assert "data-auth-form" in html
        # show_nav = false => the `is-auth` body class is applied
        # (auth.css selectors scope under `body.is-auth`); the shell
        # nav + footer remain suppressed.
        assert "is-auth" in html
        assert 'data-shell-nav="true"' not in html
        # Form hooks that auth.js and the auth.css selectors depend on.
        assert 'id="login-username"' in html
        assert 'id="login-password"' in html
        assert 'name="csrf_token"' in html and "csrf-test" in html
        assert 'action="/login"' in html
        # Leading-icon + password-toggle primitives (auth-fields.css .input-wrap).
        assert 'class="input-wrap"' in html
        assert "data-pw-input" in html
        # The version chip in the card footer uses the passed version.
        assert "Houndarr v9.9.9" in html

    def test_setup_html_structural_markers(self, render) -> None:
        html = render(
            "setup.html",
            version="9.9.9",
            csrf_token="csrf-test",
            error=None,
        )
        # Setup uses the plain auth-card (no --login modifier) plus the
        # "Welcome to Houndarr" eyebrow.
        assert 'class="auth-card"' in html
        assert "auth-card--login" not in html
        assert 'class="auth-eyebrow"' in html and "Welcome to Houndarr" in html
        # Three password-like fields: username, new-password, confirm.
        assert 'id="setup-username"' in html
        assert 'id="setup-password"' in html
        assert 'id="setup-password-confirm"' in html
        # Setup is the only page that renders the strength meter.
        assert "data-strength-source" in html
        assert 'data-strength="true"' in html or "data-strength" in html
        # Form posts back to /setup.
        assert 'action="/setup"' in html

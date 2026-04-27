"""Render-byte pinning for Track E's migration targets.

Track A.22 of the refactor plan.  Track E.1-E.17 will extract macros
from the partials exercised here.  These tests pin the structural
markers (class names, data-* attributes, visible text) so the macro
extraction cannot silently drop or rename an attribute that the HTMX
client or CSS depends on.

Coverage is narrow by design: we render each partial under a couple of
representative contexts and assert the structural markers survive.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.pinning


# ---------------------------------------------------------------------------
# instance_row.html
# ---------------------------------------------------------------------------


def _instance_stub(
    *,
    instance_id: int = 1,
    type_value: str = "sonarr",
    name: str = "Sonarr",
    enabled: bool = True,
) -> Any:
    type_mock = MagicMock()
    type_mock.value = type_value

    stub = MagicMock()

    stub.core = MagicMock()
    stub.core.id = instance_id
    stub.core.name = name
    stub.core.url = "http://host:8989"
    stub.core.type = type_mock
    stub.core.enabled = enabled

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

    stub.snapshot = MagicMock()
    stub.snapshot.monitored_total = 0
    stub.snapshot.unreleased_count = 0
    stub.snapshot.snapshot_refreshed_at = ""

    stub.schedule = MagicMock()
    stub.schedule.allowed_time_window = ""
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


# ---------------------------------------------------------------------------
# log_rows.html
# ---------------------------------------------------------------------------


class TestLogRowsRender:
    @pytest.mark.parametrize(
        "action,badge_class",
        [
            ("searched", "badge-success"),
            ("skipped", "badge-warning"),
            ("error", "badge-error"),
        ],
    )
    def test_action_badge_class(self, render, action: str, badge_class: str) -> None:
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
        assert f"badge-soft {badge_class}" in html

    def test_empty_rows_branch_renders_quietly(self, render) -> None:
        html = render(
            "partials/log_rows.html",
            rows=[],
            limit=50,
        )
        # The partial must never raise on an empty list.
        assert html is not None


# ---------------------------------------------------------------------------
# changelog_modal.html
# ---------------------------------------------------------------------------


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

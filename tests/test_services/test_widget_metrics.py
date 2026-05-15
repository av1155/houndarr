"""Tests for widget summary aggregation."""

from __future__ import annotations

import pytest

from houndarr.services.widget_metrics import compute_widget_summary


@pytest.mark.pinning
@pytest.mark.parametrize(
    ("envelope", "searches_7d", "expected"),
    [
        (
            {
                "instances": [
                    {
                        "enabled": True,
                        "active_error": None,
                        "monitored_total": 10,
                        "unreleased_count": 1,
                        "cooldown_breakdown": {"missing": 3, "cutoff": 2, "upgrade": 4},
                    }
                ]
            },
            7,
            {"tracked": 14, "eligible": 4, "gated": 5, "unreleased": 1, "searches_7d": 7},
        ),
        (
            {
                "instances": [
                    {
                        "enabled": True,
                        "active_error": None,
                        "monitored_total": 5,
                        "unreleased_count": 0,
                        "cooldown_breakdown": {"missing": 10, "cutoff": 0, "upgrade": 0},
                    },
                    {
                        "enabled": True,
                        "active_error": None,
                        "monitored_total": 8,
                        "unreleased_count": 1,
                        "cooldown_breakdown": {"missing": 1, "cutoff": 1, "upgrade": 0},
                    },
                ]
            },
            3,
            {"tracked": 18, "eligible": 5, "gated": 12, "unreleased": 1, "searches_7d": 3},
        ),
        (
            {
                "instances": [
                    {
                        "enabled": False,
                        "active_error": None,
                        "monitored_total": 99,
                        "unreleased_count": 0,
                        "cooldown_breakdown": {"missing": 99, "cutoff": 0, "upgrade": 0},
                    },
                    {
                        "enabled": True,
                        "active_error": "offline",
                        "monitored_total": 99,
                        "unreleased_count": 0,
                        "cooldown_breakdown": {"missing": 99, "cutoff": 0, "upgrade": 0},
                    },
                ]
            },
            2,
            {"tracked": 0, "eligible": 0, "gated": 0, "unreleased": 0, "searches_7d": 2},
        ),
    ],
)
def test_compute_widget_summary_matches_dashboard_contract(
    envelope: dict[str, object],
    searches_7d: int,
    expected: dict[str, int],
) -> None:
    assert compute_widget_summary(envelope, searches_7d) == expected


def test_compute_widget_summary_tolerates_missing_fields() -> None:
    summary = compute_widget_summary({"instances": [{"enabled": True, "active_error": None}]}, -1)
    assert summary == {"tracked": 0, "eligible": 0, "gated": 0, "unreleased": 0, "searches_7d": 0}

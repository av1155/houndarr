"""Pin the pure helpers in engine/search_loop.py.

Track A.16 of the refactor plan.  Track D.21 will collapse the 21-param
signature of ``_run_search_pass`` into a ``SearchPassConfig`` dataclass,
and Track B.14/B.15 will wrap dispatch errors into typed exceptions.
These tests lock the pre-refactor behaviour of the pure helpers
(``_clamp``, the four page-size / scan-budget bounders, and
``_is_release_timing_reason``) so nothing drifts silently.
"""

from __future__ import annotations

import pytest

from houndarr.engine.search_loop import (
    _clamp,
    _cutoff_page_size,
    _cutoff_scan_budget,
    _is_release_timing_reason,
    _missing_page_size,
    _missing_scan_budget,
)

pytestmark = pytest.mark.pinning


class TestClamp:
    def test_in_range(self) -> None:
        assert _clamp(50, 10, 100) == 50

    def test_below_min(self) -> None:
        assert _clamp(5, 10, 100) == 10

    def test_above_max(self) -> None:
        assert _clamp(150, 10, 100) == 100

    def test_at_min(self) -> None:
        assert _clamp(10, 10, 100) == 10

    def test_at_max(self) -> None:
        assert _clamp(100, 10, 100) == 100

    def test_min_equals_max(self) -> None:
        """Degenerate range: value snaps to the single allowed point."""
        assert _clamp(42, 7, 7) == 7


class TestMissingPageSize:
    def test_below_min_clamped(self) -> None:
        """batch_size * 4 below the minimum floor clamps to the min (10)."""
        assert _missing_page_size(1) == 10  # 1*4=4 < 10

    def test_above_max_clamped(self) -> None:
        """batch_size * 4 above the ceiling clamps to the max (50)."""
        assert _missing_page_size(100) == 50  # 100*4=400 > 50

    def test_in_range(self) -> None:
        assert _missing_page_size(5) == 20  # 5*4=20 inside [10, 50]


class TestCutoffPageSize:
    def test_below_min_clamped(self) -> None:
        assert _cutoff_page_size(1) == 5  # 1*4=4 < 5

    def test_above_max_clamped(self) -> None:
        assert _cutoff_page_size(100) == 25  # 100*4=400 > 25

    def test_in_range(self) -> None:
        assert _cutoff_page_size(4) == 16  # 4*4=16 inside [5, 25]


class TestMissingScanBudget:
    def test_below_min_clamped(self) -> None:
        assert _missing_scan_budget(1) == 24  # 1*12=12 < 24

    def test_above_max_clamped(self) -> None:
        assert _missing_scan_budget(100) == 120  # 100*12=1200 > 120

    def test_in_range(self) -> None:
        assert _missing_scan_budget(5) == 60  # 5*12=60 inside [24, 120]


class TestCutoffScanBudget:
    def test_below_min_clamped(self) -> None:
        assert _cutoff_scan_budget(1) == 12

    def test_above_max_clamped(self) -> None:
        assert _cutoff_scan_budget(100) == 60

    def test_in_range(self) -> None:
        assert _cutoff_scan_budget(3) == 36


class TestIsReleaseTimingReason:
    def test_not_yet_released_matches(self) -> None:
        assert _is_release_timing_reason("not yet released") is True

    def test_post_release_grace_matches_any_suffix(self) -> None:
        """Any string starting with 'post-release grace' counts as release-timing."""
        assert _is_release_timing_reason("post-release grace (6 hours)") is True
        assert _is_release_timing_reason("post-release grace") is True

    def test_unrelated_reason_does_not_match(self) -> None:
        assert _is_release_timing_reason("cooldown") is False
        assert _is_release_timing_reason("queue full") is False

    def test_none_returns_false(self) -> None:
        assert _is_release_timing_reason(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert _is_release_timing_reason("") is False

    def test_prefix_case_sensitive(self) -> None:
        """Uppercase 'POST-RELEASE GRACE' does not match (pinning quirk)."""
        assert _is_release_timing_reason("POST-RELEASE GRACE") is False

"""Characterisation tests for services/time_window.py boundary behaviour.

The existing ``tests/test_services/test_time_window.py`` covers
the happy paths, format errors, and wraparound cases exhaustively.
This module pins the remaining boundary edges a refactor could
drift:

* ``parse_time_window(None)`` and ``validate_allowed_time_window(None)``
  both short-circuit without raising (pinning quirk: the runtime type
  hint is ``str`` but both functions tolerate ``None``).
* The gate predicate ignores seconds and microseconds (it rounds to the
  minute via ``_to_minutes``).
* The inclusive-start / exclusive-end boundary holds even when the wall
  clock carries sub-minute precision.
* Parse-and-format is a round-trip for any canonical spec.
* A wraparound range and a non-wraparound range can co-exist in the same
  spec; each is evaluated independently.
* Whitespace around commas inside a longer CSV is still tolerated by
  ``validate_allowed_time_window`` (not just by ``parse_time_window``).
"""

from __future__ import annotations

from datetime import time

import pytest

from houndarr.services.time_window import (
    format_ranges,
    is_within_window,
    parse_time_window,
    validate_allowed_time_window,
)

pytestmark = pytest.mark.pinning


# None input handling (runtime safety beyond the static type hint)


class TestNoneHandling:
    """Pin that both public functions tolerate ``None`` without raising."""

    def test_parse_time_window_none(self) -> None:
        """``parse_time_window(None)`` returns an empty list (no gate configured)."""
        assert parse_time_window(None) == []  # type: ignore[arg-type]

    def test_validate_none(self) -> None:
        """``validate_allowed_time_window(None)`` returns ``None`` (valid)."""
        assert validate_allowed_time_window(None) is None  # type: ignore[arg-type]


# Sub-minute precision in is_within_window


class TestSubMinuteGating:
    """Pin that the gate rounds to the minute (seconds and microseconds ignored)."""

    def test_seconds_inside_window_allowed(self) -> None:
        """``09:00:30`` is inside ``[09:00, 17:00)``."""
        ranges = [(time(9, 0), time(17, 0))]
        assert is_within_window(time(9, 0, 30), ranges) is True

    def test_microseconds_inside_window_allowed(self) -> None:
        """Sub-second precision has no effect on gating."""
        ranges = [(time(9, 0), time(17, 0))]
        assert is_within_window(time(9, 0, 0, 500_000), ranges) is True

    def test_exclusive_end_ignores_seconds(self) -> None:
        """At the exclusive-end boundary, non-zero seconds do not pull the
        clock back inside the window (pinning quirk: gate rounds to minute)."""
        ranges = [(time(9, 0), time(17, 0))]
        # 17:00:30 is after 17:00, and the gate rounds to 17:00 which is
        # exclusive; stays outside.
        assert is_within_window(time(17, 0, 30), ranges) is False

    def test_one_minute_before_end_allowed(self) -> None:
        """``16:59`` is allowed inside ``[09:00, 17:00)``."""
        ranges = [(time(9, 0), time(17, 0))]
        assert is_within_window(time(16, 59), ranges) is True


# Round-trip parse + format


class TestParseFormatRoundTrip:
    """Pin that canonical specs round-trip unchanged."""

    @pytest.mark.parametrize(
        "spec",
        [
            "09:00-23:00",
            "09:00-12:00,14:00-22:00",
            "22:00-06:00",
            "22:00-00:00",
            "00:00-06:00",
        ],
    )
    def test_roundtrip_canonical(self, spec: str) -> None:
        """``format_ranges(parse_time_window(spec)) == spec`` for canonical input."""
        assert format_ranges(parse_time_window(spec)) == spec

    def test_roundtrip_normalises_whitespace(self) -> None:
        """Input with interior whitespace normalises to the canonical form."""
        raw = "  09:00-12:00 , 14:00-22:00  "
        canonical = "09:00-12:00,14:00-22:00"
        assert format_ranges(parse_time_window(raw)) == canonical


# Mixed wraparound / non-wraparound ranges


class TestMixedWraparoundRanges:
    """Pin that wraparound and standard ranges co-exist in the same spec."""

    def test_mixed_spec_allows_wraparound_member(self) -> None:
        """A wrap range (``22:00-06:00``) and a day range (``09:00-12:00``)
        in the same spec both gate independently: 23:00 is allowed via the wrap."""
        ranges = parse_time_window("09:00-12:00,22:00-06:00")
        assert is_within_window(time(23, 0), ranges) is True

    def test_mixed_spec_allows_day_member(self) -> None:
        """Same spec, 10:30 is allowed via the day range."""
        ranges = parse_time_window("09:00-12:00,22:00-06:00")
        assert is_within_window(time(10, 30), ranges) is True

    def test_mixed_spec_rejects_gap(self) -> None:
        """Same spec, 14:00 falls outside both ranges."""
        ranges = parse_time_window("09:00-12:00,22:00-06:00")
        assert is_within_window(time(14, 0), ranges) is False


# validate_allowed_time_window message stability


class TestValidatorMessages:
    """Pin that validator messages are stable literals and stay constant."""

    def test_too_many_ranges_message_shape(self) -> None:
        """Pin the exact message body for the 24-range cap."""
        spec = ",".join(["09:00-10:00"] * 25)
        msg = validate_allowed_time_window(spec)
        assert msg == "Too many time ranges (max 24)."

    def test_empty_piece_message_is_literal(self) -> None:
        """Pin the exact literal used for empty-piece rejection."""
        msg = validate_allowed_time_window("09:00-12:00,,14:00-22:00")
        assert msg == "Empty time range in allowed-window spec."

    def test_invalid_format_message_is_literal(self) -> None:
        """Pin the exact literal used for format-rejection."""
        msg = validate_allowed_time_window("9:00-17:00")
        assert msg == "Invalid time range format. Use HH:MM-HH:MM (e.g. 09:00-23:00)."

    def test_zero_duration_message_is_literal(self) -> None:
        """Pin the exact literal for zero-duration rejection."""
        msg = validate_allowed_time_window("09:00-09:00")
        assert msg == "Time range has no duration (start equals end)."

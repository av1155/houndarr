"""Tests for the allowed-time-window parser and gate predicate."""

from __future__ import annotations

from datetime import time

import pytest

from houndarr.services.time_window import (
    format_ranges,
    is_within_window,
    parse_time_window,
    validate_allowed_time_window,
)

# ---------------------------------------------------------------------------
# parse_time_window
# ---------------------------------------------------------------------------


def test_empty_string_means_unlimited() -> None:
    assert parse_time_window("") == []


def test_whitespace_only_means_unlimited() -> None:
    assert parse_time_window("   \t  ") == []


def test_single_range_is_parsed() -> None:
    assert parse_time_window("09:00-23:00") == [(time(9, 0), time(23, 0))]


def test_multiple_ranges_are_parsed_in_order() -> None:
    ranges = parse_time_window("09:00-12:00,14:00-22:00")
    assert ranges == [
        (time(9, 0), time(12, 0)),
        (time(14, 0), time(22, 0)),
    ]


def test_whitespace_around_comma_is_tolerated() -> None:
    ranges = parse_time_window("09:00-12:00 , 14:00-22:00")
    assert ranges == [
        (time(9, 0), time(12, 0)),
        (time(14, 0), time(22, 0)),
    ]


def test_leading_and_trailing_whitespace_stripped() -> None:
    assert parse_time_window("  09:00-23:00  ") == [(time(9, 0), time(23, 0))]


def test_wraparound_range_is_parsed() -> None:
    # 22:00-06:00 is valid; the gate handles the wraparound.
    assert parse_time_window("22:00-06:00") == [(time(22, 0), time(6, 0))]


def test_end_at_midnight_is_parsed() -> None:
    # 22:00-00:00 is valid and means "the last two hours of the day".
    assert parse_time_window("22:00-00:00") == [(time(22, 0), time(0, 0))]


def test_start_at_midnight_is_parsed() -> None:
    assert parse_time_window("00:00-06:00") == [(time(0, 0), time(6, 0))]


@pytest.mark.parametrize(
    "spec",
    [
        "9:00-17:00",  # single-digit hour
        "09:0-17:00",  # single-digit minute
        "09:00",  # missing end
        "09:00-",  # empty end
        "-17:00",  # empty start
        "abc",  # garbage
        "09:00-17:00-20:00",  # extra dash
        "09.00-17.00",  # wrong separator
    ],
)
def test_malformed_range_is_rejected(spec: str) -> None:
    with pytest.raises(ValueError, match="Invalid time range"):
        parse_time_window(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "25:00-30:00",  # hour > 23
        "09:60-10:00",  # minute > 59
        "09:00-10:99",
    ],
)
def test_out_of_range_time_is_rejected(spec: str) -> None:
    with pytest.raises(ValueError, match="Out-of-range"):
        parse_time_window(spec)


def test_zero_width_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="no duration"):
        parse_time_window("09:00-09:00")


def test_zero_width_at_midnight_is_rejected() -> None:
    with pytest.raises(ValueError, match="no duration"):
        parse_time_window("00:00-00:00")


def test_empty_range_in_csv_is_rejected() -> None:
    with pytest.raises(ValueError, match="Empty time range"):
        parse_time_window("09:00-12:00,,14:00-22:00")


def test_trailing_comma_is_rejected() -> None:
    with pytest.raises(ValueError, match="Empty time range"):
        parse_time_window("09:00-12:00,")


def test_too_many_ranges_is_rejected() -> None:
    spec = ",".join(["09:00-10:00"] * 25)
    with pytest.raises(ValueError, match="Too many"):
        parse_time_window(spec)


def test_overlapping_ranges_are_accepted() -> None:
    # Do not merge; any range matching is sufficient at gate time.
    ranges = parse_time_window("09:00-12:00,11:00-13:00")
    assert len(ranges) == 2


# ---------------------------------------------------------------------------
# is_within_window
# ---------------------------------------------------------------------------


def test_empty_ranges_always_allow() -> None:
    assert is_within_window(time(3, 0), []) is True
    assert is_within_window(time(23, 59), []) is True


def test_simple_range_inside() -> None:
    ranges = [(time(9, 0), time(17, 0))]
    assert is_within_window(time(12, 0), ranges) is True


def test_simple_range_outside_before() -> None:
    ranges = [(time(9, 0), time(17, 0))]
    assert is_within_window(time(8, 59), ranges) is False


def test_simple_range_outside_after() -> None:
    ranges = [(time(9, 0), time(17, 0))]
    assert is_within_window(time(17, 30), ranges) is False


def test_simple_range_start_inclusive() -> None:
    ranges = [(time(9, 0), time(17, 0))]
    assert is_within_window(time(9, 0), ranges) is True


def test_simple_range_end_exclusive() -> None:
    ranges = [(time(9, 0), time(17, 0))]
    assert is_within_window(time(17, 0), ranges) is False


def test_wraparound_allows_late_evening() -> None:
    ranges = [(time(22, 0), time(6, 0))]
    assert is_within_window(time(23, 0), ranges) is True


def test_wraparound_allows_early_morning() -> None:
    ranges = [(time(22, 0), time(6, 0))]
    assert is_within_window(time(5, 30), ranges) is True


def test_wraparound_rejects_midday() -> None:
    ranges = [(time(22, 0), time(6, 0))]
    assert is_within_window(time(12, 0), ranges) is False


def test_wraparound_start_inclusive() -> None:
    ranges = [(time(22, 0), time(6, 0))]
    assert is_within_window(time(22, 0), ranges) is True


def test_wraparound_end_exclusive() -> None:
    ranges = [(time(22, 0), time(6, 0))]
    assert is_within_window(time(6, 0), ranges) is False


def test_end_at_midnight_allows_23_59() -> None:
    ranges = [(time(22, 0), time(0, 0))]
    assert is_within_window(time(23, 59), ranges) is True


def test_end_at_midnight_blocks_midnight_itself() -> None:
    # End is exclusive; 00:00 is the end of the wraparound region.
    ranges = [(time(22, 0), time(0, 0))]
    assert is_within_window(time(0, 0), ranges) is False


def test_multiple_ranges_inside_first() -> None:
    ranges = [(time(9, 0), time(12, 0)), (time(14, 0), time(22, 0))]
    assert is_within_window(time(10, 0), ranges) is True


def test_multiple_ranges_inside_second() -> None:
    ranges = [(time(9, 0), time(12, 0)), (time(14, 0), time(22, 0))]
    assert is_within_window(time(15, 0), ranges) is True


def test_multiple_ranges_in_gap() -> None:
    ranges = [(time(9, 0), time(12, 0)), (time(14, 0), time(22, 0))]
    assert is_within_window(time(13, 0), ranges) is False


def test_overlapping_ranges_any_matching_is_enough() -> None:
    ranges = [(time(9, 0), time(12, 0)), (time(11, 0), time(13, 0))]
    assert is_within_window(time(12, 30), ranges) is True


# ---------------------------------------------------------------------------
# format_ranges
# ---------------------------------------------------------------------------


def test_format_ranges_single() -> None:
    assert format_ranges([(time(9, 0), time(23, 0))]) == "09:00-23:00"


def test_format_ranges_multiple() -> None:
    ranges = [(time(9, 0), time(12, 0)), (time(14, 0), time(22, 0))]
    assert format_ranges(ranges) == "09:00-12:00,14:00-22:00"


def test_format_ranges_empty() -> None:
    assert format_ranges([]) == ""


# ---------------------------------------------------------------------------
# validate_allowed_time_window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "   ",
        "09:00-23:00",
        "09:00-12:00,14:00-22:00",
        "22:00-06:00",
        "00:00-06:00",
        "22:00-00:00",
    ],
)
def test_validate_accepts_valid_specs(spec: str) -> None:
    assert validate_allowed_time_window(spec) is None


def test_validate_rejects_too_many_ranges() -> None:
    spec = ",".join(["09:00-10:00"] * 25)
    msg = validate_allowed_time_window(spec)
    assert msg is not None
    assert "Too many" in msg


@pytest.mark.parametrize("spec", ["09:00-12:00,,14:00-22:00", "09:00-12:00,"])
def test_validate_rejects_empty_piece(spec: str) -> None:
    msg = validate_allowed_time_window(spec)
    assert msg is not None
    assert "Empty time range" in msg


@pytest.mark.parametrize(
    "spec",
    ["9:00-17:00", "09:0-17:00", "09:00", "abc", "09:00-17:00-20:00", "09.00-17.00"],
)
def test_validate_rejects_malformed_format(spec: str) -> None:
    msg = validate_allowed_time_window(spec)
    assert msg is not None
    assert "Invalid time range format" in msg


@pytest.mark.parametrize("spec", ["25:00-30:00", "09:60-10:00", "09:00-10:99"])
def test_validate_rejects_out_of_range(spec: str) -> None:
    msg = validate_allowed_time_window(spec)
    assert msg is not None
    assert "Out-of-range" in msg


@pytest.mark.parametrize("spec", ["09:00-09:00", "00:00-00:00"])
def test_validate_rejects_zero_duration(spec: str) -> None:
    msg = validate_allowed_time_window(spec)
    assert msg is not None
    assert "no duration" in msg


def test_validate_messages_do_not_echo_user_input() -> None:
    """Error strings must be constants, so nothing from the raw spec flows
    into HTTP responses. Regression guard for CodeQL py/stack-trace-exposure."""
    suspicious_token = "<script>EVIL</script>"  # noqa: S105 - literal marker
    probe = f"09:00-12:00,{suspicious_token}"
    msg = validate_allowed_time_window(probe)
    assert msg is not None
    assert suspicious_token not in msg


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "   ",
        "09:00-23:00",
        "09:00-12:00,14:00-22:00",
        "22:00-06:00",
        "00:00-06:00",
        "not-a-range",
        "25:00-30:00",
        "09:00-09:00",
        "09:00-12:00,,14:00-22:00",
        ",".join(["09:00-10:00"] * 25),
    ],
)
def test_validator_agrees_with_parser(spec: str) -> None:
    """validate_* returns None iff parse_time_window does not raise."""
    validator_ok = validate_allowed_time_window(spec) is None
    try:
        parse_time_window(spec)
        parser_ok = True
    except ValueError:
        parser_ok = False
    assert validator_ok is parser_ok

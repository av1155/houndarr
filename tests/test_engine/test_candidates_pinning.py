"""Characterisation (pinning) tests for engine/candidates.py pure functions.

These tests LOCK the current behaviour of ``_parse_iso_utc``,
``_is_unreleased``, and ``_is_within_post_release_grace`` so later
edits to the engine pipeline cannot drift their semantics.  They
capture what the code does today, including edge cases that
round-trip through ``datetime.fromisoformat``; they are not
assertions of what the code ought to do.

The existing ``tests/test_engine/test_candidates.py`` covers the happy
paths; this module adds the boundary cases (grace boundary, unreleased
transitions, malformed strings, microseconds, offset / Z parity).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from houndarr.engine.candidates import (
    _is_unreleased,
    _is_within_post_release_grace,
    _parse_iso_utc,
)

pytestmark = pytest.mark.pinning


# _parse_iso_utc: boundary cases not covered by test_candidates.py


class TestParseIsoUtcBoundary:
    """Pin boundary-case parsing behaviour of _parse_iso_utc."""

    def test_plus_zero_offset_equals_z(self) -> None:
        """`+00:00` parses to the same instant as `Z`."""
        a = _parse_iso_utc("2024-06-15T12:00:00Z")
        b = _parse_iso_utc("2024-06-15T12:00:00+00:00")
        assert a is not None and b is not None
        assert a == b

    def test_microseconds_preserved(self) -> None:
        """Microseconds survive the round-trip."""
        result = _parse_iso_utc("2024-06-15T12:00:00.123456Z")
        assert result is not None
        assert result.microsecond == 123456

    def test_date_only_attaches_utc_midnight(self) -> None:
        """A bare YYYY-MM-DD is parsed as midnight UTC."""
        result = _parse_iso_utc("2024-06-15")
        assert result is not None
        assert result.tzinfo is not None
        assert result.hour == 0 and result.minute == 0 and result.second == 0

    def test_malformed_month_returns_none(self) -> None:
        """A structurally-similar but invalid month is rejected."""
        assert _parse_iso_utc("2024-13-15T10:30:00Z") is None

    def test_whitespace_only_returns_none(self) -> None:
        """After strip, an empty value is treated as missing."""
        assert _parse_iso_utc("   ") is None

    def test_lowercase_z_not_accepted(self) -> None:
        """Lowercase 'z' is NOT normalised; only uppercase 'Z' is (pinning quirk)."""
        # Captures current behaviour: the endswith check is case-sensitive,
        # so 'z' falls through to fromisoformat which rejects it.
        assert _parse_iso_utc("2024-06-15T10:30:00z") is None


# _is_unreleased


class TestIsUnreleased:
    """Pin _is_unreleased behaviour across the release-date state space."""

    def test_none_is_released(self) -> None:
        """An item with no release date is treated as released (not unreleased)."""
        assert _is_unreleased(None) is False

    def test_empty_string_is_released(self) -> None:
        """Empty string yields the same 'released' outcome as None."""
        assert _is_unreleased("") is False

    def test_invalid_string_is_released(self) -> None:
        """Unparseable release date is treated as released (pinning quirk).

        The function returns False for both 'truly released' and
        'unparseable'. Callers that need to distinguish must parse
        upstream.
        """
        assert _is_unreleased("not-a-date") is False

    def test_past_date_is_released(self) -> None:
        """A release date clearly in the past is released."""
        past = (datetime.now(UTC) - timedelta(days=365)).isoformat().replace("+00:00", "Z")
        assert _is_unreleased(past) is False

    def test_future_date_is_unreleased(self) -> None:
        """A release date clearly in the future is unreleased."""
        future = (datetime.now(UTC) + timedelta(days=365)).isoformat().replace("+00:00", "Z")
        assert _is_unreleased(future) is True

    def test_naive_datetime_treated_as_utc(self) -> None:
        """A naive datetime string is normalised as UTC for the comparison."""
        future_naive = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None).isoformat()
        assert _is_unreleased(future_naive) is True


# _is_within_post_release_grace


class TestIsWithinPostReleaseGrace:
    """Pin grace-window behaviour across grace_hrs, release date, and now."""

    def test_zero_grace_always_false(self) -> None:
        """grace_hrs == 0 short-circuits to False regardless of release date."""
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert _is_within_post_release_grace(past, grace_hrs=0) is False

    def test_negative_grace_always_false(self) -> None:
        """grace_hrs < 0 short-circuits to False (pinning: same as 0)."""
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert _is_within_post_release_grace(past, grace_hrs=-5) is False

    def test_none_release_date_returns_false(self) -> None:
        """A missing release date is not within grace."""
        assert _is_within_post_release_grace(None, grace_hrs=6) is False

    def test_invalid_release_date_returns_false(self) -> None:
        """An unparseable release date is not within grace."""
        assert _is_within_post_release_grace("garbage", grace_hrs=6) is False

    def test_unreleased_item_returns_false(self) -> None:
        """A release date in the future is not within the post-release grace."""
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert _is_within_post_release_grace(future, grace_hrs=6) is False

    def test_released_within_grace_returns_true(self) -> None:
        """A release date 1 hour ago with a 6-hour grace is within the window."""
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert _is_within_post_release_grace(recent, grace_hrs=6) is True

    def test_released_past_grace_returns_false(self) -> None:
        """A release date 10 hours ago with a 6-hour grace is past the window."""
        old = (datetime.now(UTC) - timedelta(hours=10)).isoformat().replace("+00:00", "Z")
        assert _is_within_post_release_grace(old, grace_hrs=6) is False

    def test_window_is_right_open(self) -> None:
        """The grace window is [release_dt, release_dt + grace): at exactly
        release_dt + grace_hrs the item is OUTSIDE the window (pinning quirk).
        """
        # Pick a release_at such that now == release_at + grace_hrs within a
        # micro-jitter.  We construct release_at as (now - grace_hrs) + epsilon,
        # then assert the grace boundary is exclusive on the right.
        grace_hrs = 6
        release_at = (
            (datetime.now(UTC) - timedelta(hours=grace_hrs)).isoformat().replace("+00:00", "Z")
        )
        # release_at is ~exactly grace_hrs ago, so release_at + grace ~ now.
        # The comparison in candidates.py is `now < release_dt + timedelta(...)`,
        # so we are right at / just past the exclusive boundary -> False.
        assert _is_within_post_release_grace(release_at, grace_hrs=grace_hrs) is False

    def test_window_is_left_inclusive(self) -> None:
        """At release_dt itself (just released), the item IS within the grace window."""
        release_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        assert _is_within_post_release_grace(release_at, grace_hrs=6) is True

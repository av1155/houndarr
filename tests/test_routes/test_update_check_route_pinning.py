"""Pin the route-level helpers for the Admin > Updates panel.

Track A.9 of the refactor plan.  Focus is the ``_timeago`` filter and the
three-route response shape (status, refresh, preferences) — the Python
surface that the Jinja partial depends on.  The underlying
``services.update_check`` module already has its own tests; here we pin
the route-level behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from houndarr.routes.update_check import _timeago

pytestmark = pytest.mark.pinning


class TestTimeagoFilter:
    """Pin the coarse humanisation used by the Updates status partial."""

    def test_none_returns_empty_string(self) -> None:
        assert _timeago(None) == ""

    def test_sub_minute_returns_just_now(self) -> None:
        """A delta under 60s reads 'just now', never '0 minutes ago'."""
        recent = datetime.now(UTC) - timedelta(seconds=30)
        assert _timeago(recent) == "just now"

    def test_exactly_one_minute_is_singular(self) -> None:
        recent = datetime.now(UTC) - timedelta(minutes=1, seconds=1)
        assert _timeago(recent) == "1 minute ago"

    def test_plural_minutes(self) -> None:
        recent = datetime.now(UTC) - timedelta(minutes=5)
        assert _timeago(recent) == "5 minutes ago"

    def test_boundary_at_one_hour_switches_to_hours(self) -> None:
        """At 1h + 1s the label switches to the hours branch."""
        recent = datetime.now(UTC) - timedelta(hours=1, seconds=1)
        assert _timeago(recent) == "1 hour ago"

    def test_plural_hours(self) -> None:
        recent = datetime.now(UTC) - timedelta(hours=5)
        assert _timeago(recent) == "5 hours ago"

    def test_boundary_at_one_day_switches_to_days(self) -> None:
        recent = datetime.now(UTC) - timedelta(days=1, seconds=1)
        assert _timeago(recent) == "1 day ago"

    def test_plural_days(self) -> None:
        recent = datetime.now(UTC) - timedelta(days=10)
        assert _timeago(recent) == "10 days ago"

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive input is coerced to UTC before the delta is computed."""
        recent_naive = (datetime.now(UTC) - timedelta(minutes=3)).replace(tzinfo=None)
        assert _timeago(recent_naive) == "3 minutes ago"

    def test_future_timestamp_returns_just_now(self) -> None:
        """A future timestamp (clock skew) falls into the sub-60s branch."""
        future = datetime.now(UTC) + timedelta(seconds=5)
        assert _timeago(future) == "just now"

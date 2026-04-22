"""Tests for the SearchCandidate model and shared helper functions."""

from __future__ import annotations

import pytest

from houndarr.engine.candidates import (
    SearchCandidate,
    _parse_iso_utc,
)

# ---------------------------------------------------------------------------
# SearchCandidate dataclass
# ---------------------------------------------------------------------------


class TestSearchCandidate:
    """Verify the SearchCandidate frozen dataclass contract."""

    def test_frozen(self):
        """Assigning to a field raises an error."""
        candidate = SearchCandidate(
            item_id=1,
            item_type="episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "EpisodeSearch", "episode_id": 1},
        )
        with pytest.raises(AttributeError):
            candidate.item_id = 2  # type: ignore[misc]

    def test_fields(self):
        """All six fields are accessible with correct values."""
        payload = {"command": "MoviesSearch", "movie_id": 42}
        candidate = SearchCandidate(
            item_id=42,
            item_type="movie",
            label="My Movie (2024)",
            unreleased_reason="radarr reports not available",
            group_key=None,
            search_payload=payload,
        )
        assert candidate.item_id == 42
        assert candidate.item_type == "movie"
        assert candidate.label == "My Movie (2024)"
        assert candidate.unreleased_reason == "radarr reports not available"
        assert candidate.group_key is None
        assert candidate.search_payload == payload

    def test_group_key_with_value(self):
        """group_key can hold a (series_id, season) tuple."""
        candidate = SearchCandidate(
            item_id=-55003,
            item_type="episode",
            label="Show - S03 (season-context)",
            unreleased_reason=None,
            group_key=(55, 3),
            search_payload={"command": "SeasonSearch", "series_id": 55, "season_number": 3},
        )
        assert candidate.group_key == (55, 3)


# ---------------------------------------------------------------------------
# _parse_iso_utc
# ---------------------------------------------------------------------------


class TestParseIsoUtc:
    """Verify ISO-8601 parsing matches the search_loop.py original."""

    def test_valid_z_suffix(self):
        """Parses a standard UTC timestamp with Z suffix."""
        result = _parse_iso_utc("2024-01-15T10:30:00Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.tzinfo is not None

    def test_with_offset(self):
        """Parses a timestamp with a timezone offset and converts to UTC."""
        result = _parse_iso_utc("2024-01-15T15:30:00+05:00")
        assert result is not None
        assert result.hour == 10
        assert result.minute == 30

    def test_naive_datetime(self):
        """Parses a naive datetime and attaches UTC."""
        result = _parse_iso_utc("2024-01-15T10:30:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.hour == 10

    def test_none_input(self):
        """Returns None for None input."""
        assert _parse_iso_utc(None) is None

    def test_empty_string(self):
        """Returns None for empty string."""
        assert _parse_iso_utc("") is None

    def test_invalid_string(self):
        """Returns None for unparseable string."""
        assert _parse_iso_utc("not-a-date") is None

    def test_whitespace_stripping(self):
        """Strips leading/trailing whitespace before parsing."""
        result = _parse_iso_utc("  2024-01-15T10:30:00Z  ")
        assert result is not None
        assert result.year == 2024

"""Tests for the Radarr adapter functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients.radarr import MissingMovie, RadarrClient
from houndarr.engine.adapters.radarr import (
    _movie_label,
    _radarr_release_anchor,
    _radarr_unreleased_reason,
    adapt_cutoff,
    adapt_missing,
    dispatch_search,
    make_client,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, InstanceType, SonarrSearchMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_DATE = "2020-01-01T00:00:00Z"


def _make_instance(*, post_release_grace_hrs: int = 24) -> Instance:
    return Instance(
        id=2,
        name="Radarr Test",
        type=InstanceType.radarr,
        url="http://radarr:7878",
        api_key="test-key",
        enabled=True,
        batch_size=10,
        sleep_interval_mins=15,
        hourly_cap=20,
        cooldown_days=7,
        post_release_grace_hrs=post_release_grace_hrs,
        queue_limit=0,
        cutoff_enabled=False,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        sonarr_search_mode=SonarrSearchMode.episode,
    )


def _make_movie(
    *,
    movie_id: int = 201,
    title: str = "My Movie",
    year: int = 2023,
    status: str | None = "released",
    minimum_availability: str | None = "released",
    is_available: bool | None = True,
    in_cinemas: str | None = "2023-01-01T00:00:00Z",
    physical_release: str | None = "2023-02-01T00:00:00Z",
    release_date: str | None = "2023-02-01T00:00:00Z",
    digital_release: str | None = None,
) -> MissingMovie:
    return MissingMovie(
        movie_id=movie_id,
        title=title,
        year=year,
        status=status,
        minimum_availability=minimum_availability,
        is_available=is_available,
        in_cinemas=in_cinemas,
        physical_release=physical_release,
        release_date=release_date,
        digital_release=digital_release,
    )


# ---------------------------------------------------------------------------
# _movie_label
# ---------------------------------------------------------------------------


class TestMovieLabel:
    """Verify _movie_label output matches search_loop.py."""

    def test_with_year(self):
        item = _make_movie(title="Inception", year=2010)
        assert _movie_label(item) == "Inception (2010)"

    def test_no_year(self):
        item = _make_movie(title="Unknown", year=0)
        assert _movie_label(item) == "Unknown"

    def test_unknown_title(self):
        item = _make_movie(title="", year=2024)
        assert _movie_label(item) == "Unknown Movie (2024)"


# ---------------------------------------------------------------------------
# _radarr_release_anchor
# ---------------------------------------------------------------------------


class TestRadarrReleaseAnchor:
    """Verify release anchor fallback order matches search_loop.py."""

    def test_digital_first(self):
        movie = _make_movie(
            digital_release="digital",
            physical_release="physical",
            release_date="release",
            in_cinemas="cinemas",
        )
        assert _radarr_release_anchor(movie) == "digital"

    def test_physical_fallback(self):
        movie = _make_movie(
            digital_release=None,
            physical_release="physical",
            release_date="release",
            in_cinemas="cinemas",
        )
        assert _radarr_release_anchor(movie) == "physical"

    def test_release_date_fallback(self):
        movie = _make_movie(
            digital_release=None,
            physical_release=None,
            release_date="release",
            in_cinemas="cinemas",
        )
        assert _radarr_release_anchor(movie) == "release"

    def test_in_cinemas_fallback(self):
        movie = _make_movie(
            digital_release=None,
            physical_release=None,
            release_date=None,
            in_cinemas="cinemas",
        )
        assert _radarr_release_anchor(movie) == "cinemas"

    def test_all_none(self):
        movie = _make_movie(
            digital_release=None,
            physical_release=None,
            release_date=None,
            in_cinemas=None,
        )
        assert _radarr_release_anchor(movie) is None


# ---------------------------------------------------------------------------
# _radarr_unreleased_reason — all 4 layers
# ---------------------------------------------------------------------------


class TestRadarrUnreleasedReason:
    """Verify all 4 layers of unreleased logic match search_loop.py."""

    def test_released_movie(self):
        """Fully released movie returns None."""
        movie = _make_movie()
        assert _radarr_unreleased_reason(movie, 24) is None

    def test_layer1_unreleased_delay(self):
        """Movie within delay window triggers layer 1."""
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        movie = _make_movie(digital_release=recent, is_available=True)
        assert _radarr_unreleased_reason(movie, 24) == "post-release grace (24h)"

    def test_layer2_not_available(self):
        """is_available=False triggers layer 2."""
        movie = _make_movie(is_available=False)
        assert _radarr_unreleased_reason(movie, 24) == "radarr reports not available"

    def test_layer3_tba_status(self):
        """status='tba' with is_available not True triggers layer 3."""
        movie = _make_movie(status="tba", is_available=None)
        assert _radarr_unreleased_reason(movie, 24) == "radarr status indicates unreleased"

    def test_layer3_announced_status(self):
        """status='announced' with is_available not True triggers layer 3."""
        movie = _make_movie(status="announced", is_available=None)
        assert _radarr_unreleased_reason(movie, 24) == "radarr status indicates unreleased"

    def test_layer3_skipped_when_available(self):
        """status='tba' but is_available=True does NOT trigger layer 3."""
        movie = _make_movie(status="tba", is_available=True)
        # Layer 2 skipped because is_available is not False
        # Layer 3 skipped because is_available is True
        assert _radarr_unreleased_reason(movie, 24) is None

    def test_layer4_future_year(self):
        """Future year with is_available not True and status not released."""
        future_year = datetime.now(UTC).year + 2
        movie = _make_movie(year=future_year, status="announced", is_available=None)
        # Layer 3 triggers first for 'announced' status
        assert _radarr_unreleased_reason(movie, 24) == "radarr status indicates unreleased"

    def test_layer4_future_year_non_announced(self):
        """Future year with non-announced, non-released status."""
        future_year = datetime.now(UTC).year + 2
        movie = _make_movie(year=future_year, status="predb", is_available=None)
        assert _radarr_unreleased_reason(movie, 24) == "future title not yet available"

    def test_layer4_skipped_when_released(self):
        """Future year but status='released' does NOT trigger layer 4."""
        future_year = datetime.now(UTC).year + 2
        movie = _make_movie(year=future_year, status="released", is_available=None)
        assert _radarr_unreleased_reason(movie, 24) is None

    def test_null_status(self):
        """None status does not crash (treated as empty string)."""
        movie = _make_movie(status=None, is_available=True)
        assert _radarr_unreleased_reason(movie, 24) is None

    def test_all_dates_none_current_year(self):
        """All date fields None with current year and is_available=None."""
        movie = _make_movie(
            digital_release=None,
            physical_release=None,
            release_date=None,
            in_cinemas=None,
            status=None,
            is_available=None,
            year=datetime.now(UTC).year,
        )
        # Layer 1: anchor is None → delay returns False.
        # Layer 2: is_available is not False → skip.
        # Layer 3: status="" not in unreleased set → skip.
        # Layer 4: year == current_year, not > → skip.
        assert _radarr_unreleased_reason(movie, 24) is None

    def test_all_dates_none_future_year(self):
        """All date fields None with future year triggers layer 4."""
        future_year = datetime.now(UTC).year + 5
        movie = _make_movie(
            digital_release=None,
            physical_release=None,
            release_date=None,
            in_cinemas=None,
            status=None,
            is_available=None,
            year=future_year,
        )
        assert _radarr_unreleased_reason(movie, 24) == "future title not yet available"

    def test_boundary_exact_delay(self):
        """Release date exactly past the delay window is eligible."""
        exactly_past = (datetime.now(UTC) - timedelta(hours=24, seconds=1)).isoformat()
        movie = _make_movie(digital_release=exactly_past, is_available=True)
        assert _radarr_unreleased_reason(movie, 24) is None

    def test_layer4_future_year_released_available(self):
        """Future year + status='released' + is_available=True bypasses all layers."""
        future_year = datetime.now(UTC).year + 2
        movie = _make_movie(year=future_year, status="released", is_available=True)
        assert _radarr_unreleased_reason(movie, 24) is None


# ---------------------------------------------------------------------------
# adapt_missing
# ---------------------------------------------------------------------------


class TestAdaptMissing:
    """Verify adapt_missing for Radarr movies."""

    def test_basic_fields(self):
        instance = _make_instance()
        item = _make_movie()
        candidate = adapt_missing(item, instance)

        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 201
        assert candidate.item_type == "movie"
        assert candidate.label == "My Movie (2023)"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "MoviesSearch", "movie_id": 201}

    def test_released_no_unreleased_reason(self):
        instance = _make_instance()
        item = _make_movie()
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_unreleased_delay(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_movie(digital_release=recent, is_available=True)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"

    def test_not_available(self):
        instance = _make_instance()
        item = _make_movie(is_available=False)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason == "radarr reports not available"


# ---------------------------------------------------------------------------
# adapt_cutoff
# ---------------------------------------------------------------------------


class TestAdaptCutoff:
    """Verify adapt_cutoff produces same output as adapt_missing."""

    def test_same_as_missing(self):
        instance = _make_instance()
        item = _make_movie()
        missing_candidate = adapt_missing(item, instance)
        cutoff_candidate = adapt_cutoff(item, instance)

        assert missing_candidate.item_id == cutoff_candidate.item_id
        assert missing_candidate.item_type == cutoff_candidate.item_type
        assert missing_candidate.label == cutoff_candidate.label
        assert missing_candidate.unreleased_reason == cutoff_candidate.unreleased_reason
        assert missing_candidate.group_key == cutoff_candidate.group_key
        assert missing_candidate.search_payload == cutoff_candidate.search_payload

    def test_unreleased(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_movie(digital_release=recent, is_available=True)
        candidate = adapt_cutoff(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"

    def test_delegates_to_missing_for_varied_inputs(self):
        """adapt_cutoff produces identical output to adapt_missing for edge cases."""
        instance = _make_instance(post_release_grace_hrs=48)
        cases = [
            _make_movie(status="tba", is_available=None),
            _make_movie(digital_release=None, physical_release=None, in_cinemas=None),
            _make_movie(year=0, title=""),
        ]
        for movie in cases:
            assert adapt_cutoff(movie, instance) == adapt_missing(movie, instance)


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    """Verify dispatch_search calls the correct RadarrClient method."""

    @pytest.mark.asyncio()
    async def test_dispatches_search(self):
        client = AsyncMock(spec=RadarrClient)
        candidate = SearchCandidate(
            item_id=201,
            item_type="movie",
            label="My Movie (2023)",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "MoviesSearch", "movie_id": 201},
        )
        await dispatch_search(client, candidate)
        client.search.assert_awaited_once_with(201)


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    """Verify make_client returns a correctly configured RadarrClient."""

    def test_returns_radarr_client(self):
        instance = _make_instance()
        client = make_client(instance)
        assert isinstance(client, RadarrClient)

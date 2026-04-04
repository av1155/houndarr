"""Tests for Whisparr v3 adapter functions.

Whisparr v3 is Radarr-based, so adapter patterns mirror the Radarr adapter:
movie-level search, no group keys, and 4-layer unreleased eligibility logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients.whisparr_v3 import LibraryWhisparrV3Movie, MissingWhisparrV3Movie
from houndarr.engine.adapters.whisparr_v3 import (
    _movie_label,
    _release_anchor,
    _unreleased_reason,
    adapt_cutoff,
    adapt_missing,
    adapt_upgrade,
    dispatch_search,
    fetch_upgrade_pool,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, InstanceType, SonarrSearchMode

_NOW_ISO = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
_OLD_RELEASE = "2020-01-15T00:00:00Z"
_FUTURE_RELEASE = "2099-06-01T00:00:00Z"


def _make_instance(*, post_release_grace_hrs: int = 6) -> Instance:
    return Instance(
        id=1,
        name="Whisparr V3 Test",
        type=InstanceType.whisparr_v3,
        url="http://whisparr:6969",
        api_key="test-key",
        enabled=True,
        batch_size=5,
        sleep_interval_mins=30,
        hourly_cap=4,
        cooldown_days=14,
        post_release_grace_hrs=post_release_grace_hrs,
        queue_limit=0,
        cutoff_enabled=False,
        cutoff_batch_size=1,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        sonarr_search_mode=SonarrSearchMode.episode,
    )


def _make_movie(**overrides: object) -> MissingWhisparrV3Movie:
    defaults: dict[str, object] = {
        "movie_id": 201,
        "title": "Great Scene",
        "year": 2024,
        "status": "released",
        "minimum_availability": "released",
        "is_available": True,
        "in_cinemas": _OLD_RELEASE,
        "physical_release": None,
        "release_date": None,
        "digital_release": _OLD_RELEASE,
    }
    defaults.update(overrides)
    return MissingWhisparrV3Movie(**defaults)  # type: ignore[arg-type]


def _make_library_movie(**overrides: object) -> LibraryWhisparrV3Movie:
    defaults: dict[str, object] = {
        "movie_id": 301,
        "title": "Library Scene",
        "year": 2023,
        "monitored": True,
        "has_file": True,
        "cutoff_met": True,
        "in_cinemas": _OLD_RELEASE,
        "physical_release": None,
        "digital_release": _OLD_RELEASE,
    }
    defaults.update(overrides)
    return LibraryWhisparrV3Movie(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Label formatting
# ---------------------------------------------------------------------------


class TestMovieLabel:
    def test_title_and_year(self) -> None:
        assert _movie_label(_make_movie()) == "Great Scene (2024)"

    def test_no_year(self) -> None:
        assert _movie_label(_make_movie(year=0)) == "Great Scene"

    def test_empty_title(self) -> None:
        assert _movie_label(_make_movie(title="")) == "Unknown Movie (2024)"


# ---------------------------------------------------------------------------
# Release anchor
# ---------------------------------------------------------------------------


class TestReleaseAnchor:
    def test_digital_preferred(self) -> None:
        m = _make_movie(digital_release="2024-01-01", physical_release="2024-02-01")
        assert _release_anchor(m) == "2024-01-01"

    def test_falls_back_to_in_cinemas(self) -> None:
        m = _make_movie(digital_release=None, physical_release=None, release_date=None)
        assert _release_anchor(m) == _OLD_RELEASE

    def test_all_none(self) -> None:
        m = _make_movie(
            digital_release=None, physical_release=None, release_date=None, in_cinemas=None
        )
        assert _release_anchor(m) is None


# ---------------------------------------------------------------------------
# Unreleased reason
# ---------------------------------------------------------------------------


class TestUnreleasedReason:
    def test_released_returns_none(self) -> None:
        assert _unreleased_reason(_make_movie(), 6) is None

    def test_future_release_anchor(self) -> None:
        m = _make_movie(digital_release=_FUTURE_RELEASE)
        assert _unreleased_reason(m, 6) == "not yet released"

    def test_post_release_grace(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        m = _make_movie(digital_release=recent)
        assert _unreleased_reason(m, 6) == "post-release grace (6h)"

    def test_is_available_false(self) -> None:
        m = _make_movie(is_available=False)
        assert _unreleased_reason(m, 0) == "whisparr reports not available"

    def test_tba_status(self) -> None:
        m = _make_movie(status="tba", is_available=None)
        assert _unreleased_reason(m, 0) == "whisparr status indicates unreleased"

    def test_announced_status(self) -> None:
        m = _make_movie(status="announced", is_available=None)
        assert _unreleased_reason(m, 0) == "whisparr status indicates unreleased"

    def test_future_year(self) -> None:
        m = _make_movie(year=2099, status="", is_available=None)
        assert _unreleased_reason(m, 0) == "future title not yet available"


# ---------------------------------------------------------------------------
# adapt_missing
# ---------------------------------------------------------------------------


class TestAdaptMissing:
    def test_basic_fields(self) -> None:
        instance = _make_instance()
        candidate = adapt_missing(_make_movie(), instance)
        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 201
        assert candidate.item_type == "whisparr_v3_movie"
        assert candidate.label == "Great Scene (2024)"
        assert candidate.unreleased_reason is None
        assert candidate.group_key is None
        assert candidate.search_payload == {
            "command": "MoviesSearch",
            "movie_id": 201,
        }

    def test_unreleased_sets_reason(self) -> None:
        instance = _make_instance()
        movie = _make_movie(digital_release=_FUTURE_RELEASE)
        candidate = adapt_missing(movie, instance)
        assert candidate.unreleased_reason == "not yet released"


# ---------------------------------------------------------------------------
# adapt_cutoff (delegates to adapt_missing)
# ---------------------------------------------------------------------------


class TestAdaptCutoff:
    def test_same_as_missing(self) -> None:
        instance = _make_instance()
        movie = _make_movie()
        assert adapt_cutoff(movie, instance) == adapt_missing(movie, instance)


# ---------------------------------------------------------------------------
# adapt_upgrade
# ---------------------------------------------------------------------------


class TestAdaptUpgrade:
    def test_basic_fields(self) -> None:
        instance = _make_instance()
        lib = _make_library_movie()
        candidate = adapt_upgrade(lib, instance)
        assert candidate.item_id == 301
        assert candidate.item_type == "whisparr_v3_movie"
        assert candidate.unreleased_reason is None
        assert candidate.search_payload["command"] == "MoviesSearch"


# ---------------------------------------------------------------------------
# fetch_upgrade_pool
# ---------------------------------------------------------------------------


class TestFetchUpgradePool:
    @pytest.mark.asyncio()
    async def test_filters_eligible(self) -> None:
        eligible = _make_library_movie(monitored=True, has_file=True, cutoff_met=True)
        ineligible_no_file = _make_library_movie(
            movie_id=302, monitored=True, has_file=False, cutoff_met=False
        )
        ineligible_unmonitored = _make_library_movie(
            movie_id=303, monitored=False, has_file=True, cutoff_met=True
        )
        mock_client = AsyncMock()
        mock_client.get_library.return_value = [
            eligible,
            ineligible_no_file,
            ineligible_unmonitored,
        ]
        instance = _make_instance()
        pool = await fetch_upgrade_pool(mock_client, instance)
        assert len(pool) == 1
        assert pool[0].movie_id == 301


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    @pytest.mark.asyncio()
    async def test_dispatches_movie_search(self) -> None:
        mock_client = AsyncMock()
        candidate = adapt_missing(_make_movie(), _make_instance())
        await dispatch_search(mock_client, candidate)
        mock_client.search.assert_awaited_once_with(201)

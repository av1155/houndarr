"""Tests for the Whisparr adapter functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients.whisparr import MissingWhisparrEpisode, WhisparrClient
from houndarr.engine.adapters.whisparr import (
    _episode_label,
    _season_context_label,
    _season_item_id,
    _whisparr_unreleased_reason,
    adapt_cutoff,
    adapt_missing,
    dispatch_search,
    make_client,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, InstanceType, WhisparrSearchMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_RELEASE = datetime(2020, 1, 1, tzinfo=UTC)


def _make_instance(
    *,
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode,
    post_release_grace_hrs: int = 24,
) -> Instance:
    return Instance(
        id=5,
        name="Whisparr Test",
        type=InstanceType.whisparr,
        url="http://whisparr:6969",
        api_key="test-key",
        enabled=True,
        batch_size=10,
        sleep_interval_mins=15,
        hourly_cap=20,
        cooldown_days=7,
        post_release_grace_hrs=post_release_grace_hrs,
        cutoff_enabled=False,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        whisparr_search_mode=whisparr_search_mode,
    )


def _make_episode(
    *,
    episode_id: int = 501,
    series_id: int | None = 70,
    series_title: str = "My Show",
    episode_title: str = "Scene Title",
    season_number: int = 1,
    absolute_episode_number: int | None = 5,
    release_date: datetime | None = _OLD_RELEASE,
) -> MissingWhisparrEpisode:
    return MissingWhisparrEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title=series_title,
        episode_title=episode_title,
        season_number=season_number,
        absolute_episode_number=absolute_episode_number,
        release_date=release_date,
    )


# ---------------------------------------------------------------------------
# Label builders — note: Whisparr labels omit episodeNumber
# ---------------------------------------------------------------------------


class TestEpisodeLabel:
    """Verify _episode_label output (no episode number in Whisparr)."""

    def test_with_title(self):
        item = _make_episode(series_title="Series A", episode_title="Scene 1")
        assert _episode_label(item) == "Series A - S01 - Scene 1"

    def test_without_title(self):
        item = _make_episode(episode_title="")
        assert _episode_label(item) == "My Show - S01"

    def test_unknown_series(self):
        item = _make_episode(series_title="", episode_title="Ep")
        assert _episode_label(item) == "Unknown Series - S01 - Ep"

    def test_formatting(self):
        item = _make_episode(season_number=12, episode_title="Mid")
        assert _episode_label(item) == "My Show - S12 - Mid"


class TestSeasonContextLabel:
    """Verify _season_context_label output."""

    def test_basic(self):
        item = _make_episode(series_title="Show X", season_number=3)
        assert _season_context_label(item) == "Show X - S03 (season-context)"

    def test_unknown_series(self):
        item = _make_episode(series_title="", season_number=1)
        assert _season_context_label(item) == "Unknown Series - S01 (season-context)"


# ---------------------------------------------------------------------------
# Season item ID — same formula as Sonarr
# ---------------------------------------------------------------------------


class TestSeasonItemId:
    """Verify _season_item_id formula matches Sonarr's pattern."""

    def test_basic(self):
        assert _season_item_id(70, 3) == -(70 * 1000 + 3)

    def test_negative_result(self):
        assert _season_item_id(70, 3) == -70003

    def test_specials(self):
        assert _season_item_id(70, 0) == -70000

    def test_large_series(self):
        assert _season_item_id(999, 99) == -999099

    def test_distinct_from_different_series(self):
        assert _season_item_id(3, 5) != _season_item_id(5, 3)


# ---------------------------------------------------------------------------
# _whisparr_unreleased_reason (operates on datetime, not str)
# ---------------------------------------------------------------------------


class TestWhisparrUnreleasedReason:
    """Verify the Whisparr-specific unreleased reason function."""

    def test_old_date_returns_none(self):
        assert _whisparr_unreleased_reason(_OLD_RELEASE, 24) is None

    def test_recent_date_returns_grace_reason(self):
        recent = datetime.now(UTC) - timedelta(hours=1)
        assert _whisparr_unreleased_reason(recent, 24) == "post-release grace (24h)"

    def test_future_date_returns_not_yet_released(self):
        future = datetime.now(UTC) + timedelta(hours=100)
        assert _whisparr_unreleased_reason(future, 24) == "not yet released"

    def test_none_returns_none(self):
        assert _whisparr_unreleased_reason(None, 24) is None

    def test_zero_grace_returns_none(self):
        recent = datetime.now(UTC) - timedelta(hours=1)
        assert _whisparr_unreleased_reason(recent, 0) is None

    def test_negative_grace_returns_none(self):
        recent = datetime.now(UTC) - timedelta(hours=1)
        assert _whisparr_unreleased_reason(recent, -1) is None

    def test_boundary_exact_grace(self):
        exactly_past = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        assert _whisparr_unreleased_reason(exactly_past, 24) is None


# ---------------------------------------------------------------------------
# adapt_missing — episode mode
# ---------------------------------------------------------------------------


class TestAdaptMissingEpisodeMode:
    """Verify adapt_missing in episode mode."""

    def test_basic_fields(self):
        instance = _make_instance()
        item = _make_episode()
        candidate = adapt_missing(item, instance)

        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 501
        assert candidate.item_type == "whisparr_episode"
        assert candidate.label == "My Show - S01 - Scene Title"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "EpisodeSearch", "episode_id": 501}

    def test_released_no_unreleased_reason(self):
        instance = _make_instance()
        item = _make_episode(release_date=_OLD_RELEASE)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_unreleased_within_delay(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = datetime.now(UTC) - timedelta(hours=1)
        item = _make_episode(release_date=recent)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"

    def test_null_release_date_is_eligible(self):
        instance = _make_instance(post_release_grace_hrs=24)
        item = _make_episode(release_date=None)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_boundary_exact_delay(self):
        instance = _make_instance(post_release_grace_hrs=24)
        exactly_past = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        item = _make_episode(release_date=exactly_past)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None


# ---------------------------------------------------------------------------
# adapt_missing — season-context mode
# ---------------------------------------------------------------------------


class TestAdaptMissingSeasonContext:
    """Verify adapt_missing in season-context mode."""

    def test_basic_fields(self):
        instance = _make_instance(whisparr_search_mode=WhisparrSearchMode.season_context)
        item = _make_episode(series_id=70, season_number=3)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _season_item_id(70, 3)
        assert candidate.item_type == "whisparr_episode"
        assert candidate.label == "My Show - S03 (season-context)"
        assert candidate.group_key == (70, 3)
        assert candidate.search_payload == {
            "command": "SeasonSearch",
            "series_id": 70,
            "season_number": 3,
        }

    def test_null_series_id_falls_back(self):
        """When series_id is None, falls back to episode-mode behavior."""
        instance = _make_instance(whisparr_search_mode=WhisparrSearchMode.season_context)
        item = _make_episode(series_id=None)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 501  # episode_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_season_zero_falls_back(self):
        """Season 0 falls back to episode-mode behavior."""
        instance = _make_instance(whisparr_search_mode=WhisparrSearchMode.season_context)
        item = _make_episode(series_id=70, season_number=0)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 501  # episode_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_large_season_number(self):
        """High season numbers produce valid, distinct synthetic IDs."""
        instance = _make_instance(whisparr_search_mode=WhisparrSearchMode.season_context)
        item = _make_episode(series_id=1, season_number=999)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _season_item_id(1, 999)
        assert candidate.item_id < 0
        assert candidate.group_key == (1, 999)
        # Must not collide with a different series/season combination.
        other = _make_episode(series_id=999, season_number=1)
        other_candidate = adapt_missing(other, instance)
        assert candidate.item_id != other_candidate.item_id


# ---------------------------------------------------------------------------
# adapt_cutoff
# ---------------------------------------------------------------------------


class TestAdaptCutoff:
    """Verify adapt_cutoff always uses episode mode."""

    def test_episode_mode(self):
        instance = _make_instance()
        item = _make_episode()
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 501
        assert candidate.item_type == "whisparr_episode"
        assert candidate.label == "My Show - S01 - Scene Title"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "EpisodeSearch", "episode_id": 501}

    def test_ignores_season_context_mode(self):
        """Even with season_context mode, cutoff uses episode-mode."""
        instance = _make_instance(whisparr_search_mode=WhisparrSearchMode.season_context)
        item = _make_episode(series_id=70, season_number=3)
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 501  # episode_id, NOT synthetic season ID
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_unreleased(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = datetime.now(UTC) - timedelta(hours=1)
        item = _make_episode(release_date=recent)
        candidate = adapt_cutoff(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    """Verify dispatch_search calls the correct client method."""

    @pytest.mark.asyncio()
    async def test_episode_search(self):
        client = AsyncMock(spec=WhisparrClient)
        candidate = SearchCandidate(
            item_id=501,
            item_type="whisparr_episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "EpisodeSearch", "episode_id": 501},
        )
        await dispatch_search(client, candidate)
        client.search.assert_awaited_once_with(501)

    @pytest.mark.asyncio()
    async def test_season_search(self):
        client = AsyncMock(spec=WhisparrClient)
        candidate = SearchCandidate(
            item_id=-70003,
            item_type="whisparr_episode",
            label="Test",
            unreleased_reason=None,
            group_key=(70, 3),
            search_payload={"command": "SeasonSearch", "series_id": 70, "season_number": 3},
        )
        await dispatch_search(client, candidate)
        client.search_season.assert_awaited_once_with(70, 3)

    @pytest.mark.asyncio()
    async def test_unknown_command_raises(self):
        client = AsyncMock(spec=WhisparrClient)
        candidate = SearchCandidate(
            item_id=1,
            item_type="whisparr_episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "UnknownCommand"},
        )
        with pytest.raises(ValueError, match="Unknown Whisparr search command"):
            await dispatch_search(client, candidate)


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    """Verify make_client returns a correctly configured WhisparrClient."""

    def test_returns_whisparr_client(self):
        instance = _make_instance()
        client = make_client(instance)
        assert isinstance(client, WhisparrClient)

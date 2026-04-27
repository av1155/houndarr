"""Tests for the Whisparr v2 adapter functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients.whisparr_v2 import MissingWhisparrV2Episode, WhisparrV2Client
from houndarr.engine.adapters.whisparr_v2 import (
    _episode_label,
    _season_context_label,
    _season_item_id,
    _whisparr_v2_unreleased_reason,
    adapt_cutoff,
    adapt_missing,
    dispatch_search,
    fetch_instance_snapshot,
    make_client,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    MissingPolicy,
    RuntimeSnapshot,
    SchedulePolicy,
    SearchOrder,
    UpgradePolicy,
    WhisparrV2SearchMode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_RELEASE = datetime(2020, 1, 1, tzinfo=UTC)


def _make_instance(
    *,
    whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode.episode,
    post_release_grace_hrs: int = 24,
) -> Instance:
    return Instance(
        core=InstanceCore(
            id=5,
            name="Whisparr Test",
            type=InstanceType.whisparr_v2,
            url="http://whisparr:6969",
            api_key="test-key",
            enabled=True,
        ),
        missing=MissingPolicy(
            batch_size=10,
            sleep_interval_mins=15,
            hourly_cap=20,
            cooldown_days=7,
            post_release_grace_hrs=post_release_grace_hrs,
            queue_limit=0,
            whisparr_v2_search_mode=whisparr_v2_search_mode,
        ),
        cutoff=CutoffPolicy(
            cutoff_enabled=False,
            cutoff_batch_size=5,
            cutoff_cooldown_days=21,
            cutoff_hourly_cap=1,
        ),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(search_order=SearchOrder.chronological),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
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
) -> MissingWhisparrV2Episode:
    return MissingWhisparrV2Episode(
        episode_id=episode_id,
        series_id=series_id,
        series_title=series_title,
        episode_title=episode_title,
        season_number=season_number,
        absolute_episode_number=absolute_episode_number,
        release_date=release_date,
    )


# ---------------------------------------------------------------------------
# Label builders - note: Whisparr labels omit episodeNumber
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
# Season item ID - same formula as Sonarr
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
# _whisparr_v2_unreleased_reason (operates on datetime, not str)
# ---------------------------------------------------------------------------


class TestWhisparrV2UnreleasedReason:
    """Verify the Whisparr-specific unreleased reason function."""

    def test_old_date_returns_none(self):
        assert _whisparr_v2_unreleased_reason(_OLD_RELEASE, 24) is None

    def test_recent_date_returns_grace_reason(self):
        recent = datetime.now(UTC) - timedelta(hours=1)
        assert _whisparr_v2_unreleased_reason(recent, 24) == "post-release grace (24h)"

    def test_future_date_returns_not_yet_released(self):
        future = datetime.now(UTC) + timedelta(hours=100)
        assert _whisparr_v2_unreleased_reason(future, 24) == "not yet released"

    def test_none_returns_none(self):
        assert _whisparr_v2_unreleased_reason(None, 24) is None

    def test_zero_grace_returns_none(self):
        recent = datetime.now(UTC) - timedelta(hours=1)
        assert _whisparr_v2_unreleased_reason(recent, 0) is None

    def test_negative_grace_returns_none(self):
        recent = datetime.now(UTC) - timedelta(hours=1)
        assert _whisparr_v2_unreleased_reason(recent, -1) is None

    def test_boundary_exact_grace(self):
        exactly_past = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        assert _whisparr_v2_unreleased_reason(exactly_past, 24) is None


# ---------------------------------------------------------------------------
# adapt_missing - episode mode
# ---------------------------------------------------------------------------


class TestAdaptMissingEpisodeMode:
    """Verify adapt_missing in episode mode."""

    def test_null_series_id_skipped_in_episode_mode(self):
        """Orphan records (series_id=None) are skipped even in episode mode."""
        instance = _make_instance()
        item = _make_episode(series_id=None)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 501
        assert candidate.unreleased_reason == "no series linked"

    def test_season_zero_with_valid_series_id_dispatched(self):
        """Season-0 specials with a valid series_id must not be skipped."""
        instance = _make_instance()
        item = _make_episode(series_id=70, season_number=0)
        candidate = adapt_missing(item, instance)

        assert candidate.unreleased_reason is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_null_series_id_already_unreleased_preserves_original_reason(self):
        """When an orphan record is also unreleased, the release-timing reason wins."""
        instance = _make_instance(post_release_grace_hrs=24)
        recent = _OLD_RELEASE.replace(year=2099)  # far future
        item = _make_episode(series_id=None, release_date=recent)
        candidate = adapt_missing(item, instance)

        assert candidate.unreleased_reason == "not yet released"

    def test_basic_fields(self):
        instance = _make_instance()
        item = _make_episode()
        candidate = adapt_missing(item, instance)

        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 501
        assert candidate.item_type == "whisparr_v2_episode"
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
# adapt_missing - season-context mode
# ---------------------------------------------------------------------------


class TestAdaptMissingSeasonContext:
    """Verify adapt_missing in season-context mode."""

    def test_basic_fields(self):
        instance = _make_instance(whisparr_v2_search_mode=WhisparrV2SearchMode.season_context)
        item = _make_episode(series_id=70, season_number=3)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _season_item_id(70, 3)
        assert candidate.item_type == "whisparr_v2_episode"
        assert candidate.label == "My Show - S03 (season-context)"
        assert candidate.group_key == (70, 3)
        assert candidate.search_payload == {
            "command": "SeasonSearch",
            "series_id": 70,
            "season_number": 3,
        }

    def test_null_series_id_skipped(self):
        """When series_id is None, the candidate is marked as non-searchable."""
        instance = _make_instance(whisparr_v2_search_mode=WhisparrV2SearchMode.season_context)
        item = _make_episode(series_id=None)
        candidate = adapt_missing(item, instance)

        # Falls back to episode mode (no season-context without series_id)
        assert candidate.item_id == 501  # episode_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"
        # Orphan guard: search loop will skip this instead of dispatching
        assert candidate.unreleased_reason == "no series linked"

    def test_season_zero_falls_back(self):
        """Season 0 falls back to episode-mode behavior."""
        instance = _make_instance(whisparr_v2_search_mode=WhisparrV2SearchMode.season_context)
        item = _make_episode(series_id=70, season_number=0)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 501  # episode_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_large_season_number(self):
        """High season numbers produce valid, distinct synthetic IDs."""
        instance = _make_instance(whisparr_v2_search_mode=WhisparrV2SearchMode.season_context)
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
        assert candidate.item_type == "whisparr_v2_episode"
        assert candidate.label == "My Show - S01 - Scene Title"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "EpisodeSearch", "episode_id": 501}

    def test_ignores_season_context_mode(self):
        """Even with season_context mode, cutoff uses episode-mode."""
        instance = _make_instance(whisparr_v2_search_mode=WhisparrV2SearchMode.season_context)
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

    def test_null_series_id_skipped(self):
        """Orphan cutoff records (series_id=None) must be marked as non-searchable."""
        instance = _make_instance()
        item = _make_episode(series_id=None)
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 501
        assert candidate.search_payload["command"] == "EpisodeSearch"
        assert candidate.unreleased_reason == "no series linked"

    def test_season_zero_with_valid_series_id_dispatched(self):
        """Cutoff pass: season-0 specials with valid series_id must not be skipped."""
        instance = _make_instance()
        item = _make_episode(series_id=70, season_number=0)
        candidate = adapt_cutoff(item, instance)

        assert candidate.unreleased_reason is None
        assert candidate.search_payload["command"] == "EpisodeSearch"


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    """Verify dispatch_search calls the correct client method."""

    @pytest.mark.asyncio()
    async def test_episode_search(self):
        client = AsyncMock(spec=WhisparrV2Client)
        candidate = SearchCandidate(
            item_id=501,
            item_type="whisparr_v2_episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "EpisodeSearch", "episode_id": 501},
        )
        await dispatch_search(client, candidate)
        client.search.assert_awaited_once_with(501)

    @pytest.mark.asyncio()
    async def test_season_search(self):
        client = AsyncMock(spec=WhisparrV2Client)
        candidate = SearchCandidate(
            item_id=-70003,
            item_type="whisparr_v2_episode",
            label="Test",
            unreleased_reason=None,
            group_key=(70, 3),
            search_payload={"command": "SeasonSearch", "series_id": 70, "season_number": 3},
        )
        await dispatch_search(client, candidate)
        client.search_season.assert_awaited_once_with(70, 3)

    @pytest.mark.asyncio()
    async def test_unknown_command_raises(self):
        client = AsyncMock(spec=WhisparrV2Client)
        candidate = SearchCandidate(
            item_id=1,
            item_type="whisparr_v2_episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "UnknownCommand"},
        )
        with pytest.raises(ValueError, match="Unknown Whisparr v2 search command"):
            await dispatch_search(client, candidate)


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    """Verify make_client returns a correctly configured WhisparrV2Client."""

    def test_returns_whisparr_client(self):
        instance = _make_instance()
        client = make_client(instance)
        assert isinstance(client, WhisparrV2Client)


# ---------------------------------------------------------------------------
# fetch_instance_snapshot
# ---------------------------------------------------------------------------


class TestFetchInstanceSnapshot:
    """Verify the snapshot composition for Whisparr v2.

    Whisparr v2's domain model carries a pre-parsed ``datetime`` for
    ``release_date`` (handles both ISO string and ``{y, m, d}`` dict
    wire forms), so the adapter takes the datetime branch of the
    shared snapshot helper.

    Marked ``pinning`` because ``fetch_instance_snapshot`` is a new
    behavioural contract.
    """

    pytestmark = pytest.mark.pinning

    @pytest.mark.asyncio()
    async def test_paginated_walk_counts_future_anchors(self):
        future = datetime(2999, 1, 1, tzinfo=UTC)
        client = AsyncMock(spec=WhisparrV2Client)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 2, "cutoff": 1}[kind]
        client.get_missing.return_value = [
            _make_episode(episode_id=1, release_date=_OLD_RELEASE),
            _make_episode(episode_id=2, release_date=future),
        ]

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 3
        assert snap.unreleased_count == 1

    @pytest.mark.asyncio()
    async def test_null_release_date_treated_as_released(self):
        """A missing ``release_date`` must NOT count as unreleased.

        Mirrors the dispatch-time helper: a None datetime falls through
        to the "already released" branch.
        """
        client = AsyncMock(spec=WhisparrV2Client)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 1, "cutoff": 0}[kind]
        client.get_missing.return_value = [
            _make_episode(episode_id=1, release_date=None),
        ]

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 1
        assert snap.unreleased_count == 0

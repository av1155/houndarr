"""Tests for the Sonarr adapter functions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients._wire_models import ArrSeries
from houndarr.clients.sonarr import LibraryEpisode, MissingEpisode, SonarrClient
from houndarr.engine.adapters.sonarr import (
    _episode_label,
    _season_context_label,
    _season_item_id,
    adapt_cutoff,
    adapt_missing,
    dispatch_search,
    fetch_instance_snapshot,
    fetch_reconcile_sets,
    make_client,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, InstanceType, SonarrSearchMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_DATE = "2020-01-01T00:00:00Z"


def _make_instance(
    *,
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode,
    post_release_grace_hrs: int = 24,
) -> Instance:
    return Instance(
        id=1,
        name="Sonarr Test",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
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
        sonarr_search_mode=sonarr_search_mode,
    )


def _make_episode(
    *,
    episode_id: int = 101,
    series_id: int | None = 55,
    series_title: str = "My Show",
    episode_title: str = "Pilot",
    season: int = 1,
    episode: int = 1,
    air_date_utc: str | None = _OLD_DATE,
) -> MissingEpisode:
    return MissingEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title=series_title,
        episode_title=episode_title,
        season=season,
        episode=episode,
        air_date_utc=air_date_utc,
    )


# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


class TestEpisodeLabel:
    """Verify _episode_label output matches search_loop.py."""

    def test_with_title(self):
        item = _make_episode(series_title="Breaking Bad", episode_title="Pilot")
        assert _episode_label(item) == "Breaking Bad - S01E01 - Pilot"

    def test_without_title(self):
        item = _make_episode(episode_title="")
        assert _episode_label(item) == "My Show - S01E01"

    def test_unknown_series(self):
        item = _make_episode(series_title="", episode_title="Ep")
        assert _episode_label(item) == "Unknown Series - S01E01 - Ep"

    def test_formatting(self):
        item = _make_episode(season=12, episode=5, episode_title="Mid")
        assert _episode_label(item) == "My Show - S12E05 - Mid"


class TestSeasonContextLabel:
    """Verify _season_context_label output matches search_loop.py."""

    def test_basic(self):
        item = _make_episode(series_title="Lost", season=3)
        assert _season_context_label(item) == "Lost - S03 (season-context)"

    def test_unknown_series(self):
        item = _make_episode(series_title="", season=1)
        assert _season_context_label(item) == "Unknown Series - S01 (season-context)"


# ---------------------------------------------------------------------------
# Season item ID
# ---------------------------------------------------------------------------


class TestSeasonItemId:
    """Verify _season_item_id formula matches search_loop.py."""

    def test_basic(self):
        assert _season_item_id(55, 3) == -(55 * 1000 + 3)

    def test_negative_result(self):
        assert _season_item_id(55, 3) == -55003

    def test_specials(self):
        """Season 0 (specials) produces a valid negative ID."""
        assert _season_item_id(55, 0) == -55000

    def test_large_series(self):
        assert _season_item_id(999, 99) == -999099


# ---------------------------------------------------------------------------
# adapt_missing - episode mode
# ---------------------------------------------------------------------------


class TestAdaptMissingEpisodeMode:
    """Verify adapt_missing in episode mode."""

    def test_basic_fields(self):
        instance = _make_instance()
        item = _make_episode()
        candidate = adapt_missing(item, instance)

        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 101
        assert candidate.item_type == "episode"
        assert candidate.label == "My Show - S01E01 - Pilot"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "EpisodeSearch", "episode_id": 101}

    def test_released_no_unreleased_reason(self):
        instance = _make_instance()
        item = _make_episode(air_date_utc=_OLD_DATE)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_unreleased_within_delay(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_episode(air_date_utc=recent)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"

    def test_null_air_date_is_eligible(self):
        """Missing air_date_utc means the item is treated as eligible."""
        instance = _make_instance(post_release_grace_hrs=24)
        item = _make_episode(air_date_utc=None)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_empty_air_date_is_eligible(self):
        """Empty string air_date_utc is treated the same as None."""
        instance = _make_instance(post_release_grace_hrs=24)
        item = _make_episode(air_date_utc="")
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_boundary_exact_delay(self):
        """An item whose delay has exactly elapsed is eligible (not unreleased)."""
        instance = _make_instance(post_release_grace_hrs=24)
        exactly_past = (datetime.now(UTC) - timedelta(hours=24, seconds=1)).isoformat()
        item = _make_episode(air_date_utc=exactly_past)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None


# ---------------------------------------------------------------------------
# adapt_missing - season-context mode
# ---------------------------------------------------------------------------


class TestAdaptMissingSeasonContext:
    """Verify adapt_missing in season-context mode."""

    def test_basic_fields(self):
        instance = _make_instance(sonarr_search_mode=SonarrSearchMode.season_context)
        item = _make_episode(series_id=55, season=3)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _season_item_id(55, 3)
        assert candidate.item_type == "episode"
        assert candidate.label == "My Show - S03 (season-context)"
        assert candidate.group_key == (55, 3)
        assert candidate.search_payload == {
            "command": "SeasonSearch",
            "series_id": 55,
            "season_number": 3,
        }

    def test_null_series_id_falls_back(self):
        """When series_id is None, falls back to episode-mode behavior."""
        instance = _make_instance(sonarr_search_mode=SonarrSearchMode.season_context)
        item = _make_episode(series_id=None)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 101  # episode_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_season_zero_falls_back(self):
        """Season 0 (specials) falls back to episode-mode behavior."""
        instance = _make_instance(sonarr_search_mode=SonarrSearchMode.season_context)
        item = _make_episode(series_id=55, season=0)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 101  # episode_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_large_season_number(self):
        """High season numbers produce valid, distinct synthetic IDs."""
        instance = _make_instance(sonarr_search_mode=SonarrSearchMode.season_context)
        item = _make_episode(series_id=1, season=999)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _season_item_id(1, 999)
        assert candidate.item_id < 0
        assert candidate.group_key == (1, 999)
        # Must not collide with a different series/season combination.
        other = _make_episode(series_id=999, season=1)
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

        assert candidate.item_id == 101
        assert candidate.item_type == "episode"
        assert candidate.label == "My Show - S01E01 - Pilot"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "EpisodeSearch", "episode_id": 101}

    def test_ignores_season_context_mode(self):
        """Even with season_context mode, cutoff uses episode-mode."""
        instance = _make_instance(sonarr_search_mode=SonarrSearchMode.season_context)
        item = _make_episode(series_id=55, season=3)
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 101  # episode_id, NOT synthetic season ID
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "EpisodeSearch"

    def test_unreleased(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_episode(air_date_utc=recent)
        candidate = adapt_cutoff(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    """Verify dispatch_search calls the correct client method."""

    @pytest.mark.asyncio()
    async def test_episode_search(self):
        client = AsyncMock(spec=SonarrClient)
        candidate = SearchCandidate(
            item_id=101,
            item_type="episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "EpisodeSearch", "episode_id": 101},
        )
        await dispatch_search(client, candidate)
        client.search.assert_awaited_once_with(101)

    @pytest.mark.asyncio()
    async def test_season_search(self):
        client = AsyncMock(spec=SonarrClient)
        candidate = SearchCandidate(
            item_id=-55003,
            item_type="episode",
            label="Test",
            unreleased_reason=None,
            group_key=(55, 3),
            search_payload={"command": "SeasonSearch", "series_id": 55, "season_number": 3},
        )
        await dispatch_search(client, candidate)
        client.search_season.assert_awaited_once_with(55, 3)

    @pytest.mark.asyncio()
    async def test_unknown_command_raises(self):
        client = AsyncMock(spec=SonarrClient)
        candidate = SearchCandidate(
            item_id=1,
            item_type="episode",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "UnknownCommand"},
        )
        with pytest.raises(ValueError, match="Unknown Sonarr search command"):
            await dispatch_search(client, candidate)


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    """Verify make_client returns a correctly configured SonarrClient."""

    def test_returns_sonarr_client(self):
        instance = _make_instance()
        client = make_client(instance)
        assert isinstance(client, SonarrClient)


# ---------------------------------------------------------------------------
# fetch_reconcile_sets
# ---------------------------------------------------------------------------


def _make_library_episode(
    *,
    episode_id: int,
    series_id: int,
    season: int = 1,
    episode: int = 1,
    monitored: bool = True,
    has_file: bool = True,
    cutoff_met: bool = True,
) -> LibraryEpisode:
    return LibraryEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title=f"Series {series_id}",
        episode_title=f"Ep {episode_id}",
        season=season,
        episode=episode,
        monitored=monitored,
        has_file=has_file,
        cutoff_met=cutoff_met,
    )


class TestFetchReconcileSetsUpgrade:
    """Verify the upgrade bucket covers the FULL monitored library.

    Regression pin for the rotation-window bug: the cycle-facing
    ``fetch_upgrade_pool`` deliberately windows the series list via
    ``upgrade_series_offset`` so per-cycle indexer traffic stays
    polite.  Reconcile cannot use that window because anything
    outside the current 5-series slice would then be flagged as an
    orphan and deleted on the next snapshot refresh, silently
    collapsing ``upgrade_cooldown_days`` to one rotation period.
    """

    @pytest.mark.asyncio()
    async def test_upgrade_set_covers_every_monitored_series(self):
        """With 10 monitored series (rotation window = 5), the upgrade
        set must contain episodes from all 10, not just the windowed
        slice."""
        series_count = 10
        series_list = [
            ArrSeries(id=sid, title=f"Series {sid}", monitored=True)
            for sid in range(1, series_count + 1)
        ]
        episodes_by_series = {
            sid: [_make_library_episode(episode_id=sid * 100, series_id=sid)]
            for sid in range(1, series_count + 1)
        }

        client = AsyncMock(spec=SonarrClient)
        client.get_missing.return_value = []
        client.get_cutoff_unmet.return_value = []
        client.get_series.return_value = series_list
        client.get_episodes.side_effect = lambda series_id: episodes_by_series[series_id]

        instance = _make_instance()
        instance_with_upgrade = replace(
            instance,
            upgrade_enabled=True,
            upgrade_series_offset=0,
        )

        sets = await fetch_reconcile_sets(client, instance_with_upgrade)

        expected = frozenset(("episode", sid * 100) for sid in range(1, series_count + 1))
        assert sets.upgrade == expected

    @pytest.mark.asyncio()
    async def test_upgrade_disabled_skips_library_fetch(self):
        """Upgrade-disabled instance returns an empty upgrade set and
        never touches the ``/series`` endpoint."""
        client = AsyncMock(spec=SonarrClient)
        client.get_missing.return_value = []
        client.get_cutoff_unmet.return_value = []

        instance = _make_instance()  # UpgradePolicy() defaults to upgrade_enabled=False

        sets = await fetch_reconcile_sets(client, instance)

        assert sets.upgrade == frozenset()
        client.get_series.assert_not_called()
        client.get_episodes.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_instance_snapshot
# ---------------------------------------------------------------------------


class TestFetchInstanceSnapshot:
    """Verify the snapshot composition for Sonarr.

    monitored_total comes from get_wanted_total('missing') +
    get_wanted_total('cutoff') (cheap pageSize=1 probes).
    unreleased_count comes from a paginate_wanted walk of /wanted/missing
    counting items whose ``air_date_utc`` is strictly in the future.

    Marked ``pinning`` because ``fetch_instance_snapshot`` is a new
    behavioural contract (anchor selection, monitored vs cutoff sums,
    unreleased semantics); a future refactor of the snapshot path
    would silently drift it without this safety net.
    """

    pytestmark = pytest.mark.pinning

    @pytest.mark.asyncio()
    async def test_paginated_walk_counts_future_anchors(self):
        future = "2999-01-01T00:00:00Z"
        past = "2020-01-01T00:00:00Z"
        client = AsyncMock(spec=SonarrClient)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 3, "cutoff": 5}[kind]
        client.get_missing.return_value = [
            _make_episode(episode_id=1, air_date_utc=future),
            _make_episode(episode_id=2, air_date_utc=past),
            _make_episode(episode_id=3, air_date_utc=future),
        ]

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 8
        assert snap.unreleased_count == 2

    @pytest.mark.asyncio()
    async def test_no_unreleased_when_all_anchors_past(self):
        client = AsyncMock(spec=SonarrClient)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 2, "cutoff": 0}[kind]
        client.get_missing.return_value = [
            _make_episode(episode_id=1, air_date_utc=_OLD_DATE),
            _make_episode(episode_id=2, air_date_utc=_OLD_DATE),
        ]

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 2
        assert snap.unreleased_count == 0

    @pytest.mark.asyncio()
    async def test_null_anchor_treated_as_released(self):
        """A monitored episode without an air date is not pre-release.

        Mirrors :func:`_is_unreleased`: missing dates fall through to
        the "already released" branch so dashboards do not inflate the
        Unreleased bucket from records the *arr just hasn't classified.
        """
        client = AsyncMock(spec=SonarrClient)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 1, "cutoff": 0}[kind]
        client.get_missing.return_value = [_make_episode(episode_id=1, air_date_utc=None)]

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 1
        assert snap.unreleased_count == 0

    @pytest.mark.asyncio()
    async def test_monitored_total_uses_get_wanted_total(self):
        """monitored_total stays on the cheap pageSize=1 probe path.

        Not derived from len(get_missing()): the paginated list is
        used only for unreleased counting, while monitored_total is
        the live ``totalRecords`` for missing + cutoff so the value
        does not drift if pagination is mid-walk.
        """
        client = AsyncMock(spec=SonarrClient)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 100, "cutoff": 50}[kind]
        client.get_missing.return_value = []

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 150
        assert snap.unreleased_count == 0
        client.get_wanted_total.assert_any_await("missing")
        client.get_wanted_total.assert_any_await("cutoff")

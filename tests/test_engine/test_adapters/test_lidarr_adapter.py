"""Tests for the Lidarr adapter functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients.lidarr import LidarrClient, MissingAlbum
from houndarr.engine.adapters.lidarr import (
    _album_label,
    _artist_context_label,
    _artist_item_id,
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
    LidarrSearchMode,
    MissingPolicy,
    RuntimeSnapshot,
    SchedulePolicy,
    SearchOrder,
    UpgradePolicy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_DATE = "2020-01-01T00:00:00Z"


def _make_instance(
    *,
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album,
    post_release_grace_hrs: int = 24,
) -> Instance:
    return Instance(
        core=InstanceCore(
            id=3,
            name="Lidarr Test",
            type=InstanceType.lidarr,
            url="http://lidarr:8686",
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
            lidarr_search_mode=lidarr_search_mode,
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


def _make_album(
    *,
    album_id: int = 301,
    artist_id: int = 50,
    artist_name: str = "Test Artist",
    title: str = "Greatest Hits",
    release_date: str | None = _OLD_DATE,
) -> MissingAlbum:
    return MissingAlbum(
        album_id=album_id,
        artist_id=artist_id,
        artist_name=artist_name,
        title=title,
        release_date=release_date,
    )


# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


class TestAlbumLabel:
    """Verify _album_label output."""

    def test_basic(self):
        item = _make_album(artist_name="Pink Floyd", title="The Wall")
        assert _album_label(item) == "Pink Floyd - The Wall"

    def test_unknown_artist(self):
        item = _make_album(artist_name="", title="Some Album")
        assert _album_label(item) == "Unknown Artist - Some Album"

    def test_unknown_album(self):
        item = _make_album(artist_name="Artist", title="")
        assert _album_label(item) == "Artist - Unknown Album"

    def test_both_unknown(self):
        item = _make_album(artist_name="", title="")
        assert _album_label(item) == "Unknown Artist - Unknown Album"


class TestArtistContextLabel:
    """Verify _artist_context_label output."""

    def test_basic(self):
        item = _make_album(artist_name="Radiohead")
        assert _artist_context_label(item) == "Radiohead (artist-context)"

    def test_unknown_artist(self):
        item = _make_album(artist_name="")
        assert _artist_context_label(item) == "Unknown Artist (artist-context)"


# ---------------------------------------------------------------------------
# Artist item ID
# ---------------------------------------------------------------------------


class TestArtistItemId:
    """Verify _artist_item_id formula."""

    def test_basic(self):
        assert _artist_item_id(50) == -(50 * 1000)

    def test_negative_result(self):
        assert _artist_item_id(50) == -50000

    def test_large_artist(self):
        assert _artist_item_id(999) == -999000

    def test_distinct_ids(self):
        assert _artist_item_id(1) != _artist_item_id(2)


# ---------------------------------------------------------------------------
# adapt_missing - album mode
# ---------------------------------------------------------------------------


class TestAdaptMissingAlbumMode:
    """Verify adapt_missing in album mode."""

    def test_basic_fields(self):
        instance = _make_instance()
        item = _make_album()
        candidate = adapt_missing(item, instance)

        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 301
        assert candidate.item_type == "album"
        assert candidate.label == "Test Artist - Greatest Hits"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "AlbumSearch", "album_id": 301}

    def test_released_no_unreleased_reason(self):
        instance = _make_instance()
        item = _make_album(release_date=_OLD_DATE)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_unreleased_within_delay(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_album(release_date=recent)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"

    def test_null_release_date_is_eligible(self):
        instance = _make_instance(post_release_grace_hrs=24)
        item = _make_album(release_date=None)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_empty_release_date_is_eligible(self):
        instance = _make_instance(post_release_grace_hrs=24)
        item = _make_album(release_date="")
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_boundary_exact_delay(self):
        instance = _make_instance(post_release_grace_hrs=24)
        exactly_past = (datetime.now(UTC) - timedelta(hours=24, seconds=1)).isoformat()
        item = _make_album(release_date=exactly_past)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None


# ---------------------------------------------------------------------------
# adapt_missing - artist-context mode
# ---------------------------------------------------------------------------


class TestAdaptMissingArtistContext:
    """Verify adapt_missing in artist-context mode."""

    def test_basic_fields(self):
        instance = _make_instance(lidarr_search_mode=LidarrSearchMode.artist_context)
        item = _make_album(artist_id=50)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _artist_item_id(50)
        assert candidate.item_type == "album"
        assert candidate.label == "Test Artist (artist-context)"
        assert candidate.group_key == (50, 0)
        assert candidate.search_payload == {
            "command": "ArtistSearch",
            "artist_id": 50,
        }

    def test_zero_artist_id_falls_back(self):
        """When artist_id is 0, falls back to album-mode behavior."""
        instance = _make_instance(lidarr_search_mode=LidarrSearchMode.artist_context)
        item = _make_album(artist_id=0)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 301  # album_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "AlbumSearch"

    def test_large_artist_id(self):
        """Large artist IDs produce valid, distinct synthetic IDs."""
        instance = _make_instance(lidarr_search_mode=LidarrSearchMode.artist_context)
        item = _make_album(artist_id=999)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _artist_item_id(999)
        assert candidate.item_id < 0
        assert candidate.group_key == (999, 0)

    def test_distinct_artists_produce_distinct_ids(self):
        instance = _make_instance(lidarr_search_mode=LidarrSearchMode.artist_context)
        item_a = _make_album(artist_id=10)
        item_b = _make_album(artist_id=20)
        assert adapt_missing(item_a, instance).item_id != adapt_missing(item_b, instance).item_id


# ---------------------------------------------------------------------------
# adapt_cutoff
# ---------------------------------------------------------------------------


class TestAdaptCutoff:
    """Verify adapt_cutoff always uses album mode."""

    def test_album_mode(self):
        instance = _make_instance()
        item = _make_album()
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 301
        assert candidate.item_type == "album"
        assert candidate.label == "Test Artist - Greatest Hits"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "AlbumSearch", "album_id": 301}

    def test_ignores_artist_context_mode(self):
        """Even with artist_context mode, cutoff uses album-mode."""
        instance = _make_instance(lidarr_search_mode=LidarrSearchMode.artist_context)
        item = _make_album(artist_id=50)
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 301  # album_id, NOT synthetic artist ID
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "AlbumSearch"

    def test_unreleased(self):
        instance = _make_instance(post_release_grace_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_album(release_date=recent)
        candidate = adapt_cutoff(item, instance)
        assert candidate.unreleased_reason == "post-release grace (24h)"


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    """Verify dispatch_search calls the correct client method."""

    @pytest.mark.asyncio()
    async def test_album_search(self):
        client = AsyncMock(spec=LidarrClient)
        candidate = SearchCandidate(
            item_id=301,
            item_type="album",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "AlbumSearch", "album_id": 301},
        )
        await dispatch_search(client, candidate)
        client.search.assert_awaited_once_with(301)

    @pytest.mark.asyncio()
    async def test_artist_search(self):
        client = AsyncMock(spec=LidarrClient)
        candidate = SearchCandidate(
            item_id=-50000,
            item_type="album",
            label="Test",
            unreleased_reason=None,
            group_key=(50, 0),
            search_payload={"command": "ArtistSearch", "artist_id": 50},
        )
        await dispatch_search(client, candidate)
        client.search_artist.assert_awaited_once_with(50)

    @pytest.mark.asyncio()
    async def test_unknown_command_raises(self):
        client = AsyncMock(spec=LidarrClient)
        candidate = SearchCandidate(
            item_id=1,
            item_type="album",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "UnknownCommand"},
        )
        with pytest.raises(ValueError, match="Unknown Lidarr search command"):
            await dispatch_search(client, candidate)


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    """Verify make_client returns a correctly configured LidarrClient."""

    def test_returns_lidarr_client(self):
        instance = _make_instance()
        client = make_client(instance)
        assert isinstance(client, LidarrClient)


# ---------------------------------------------------------------------------
# fetch_instance_snapshot
# ---------------------------------------------------------------------------


class TestFetchInstanceSnapshot:
    """Verify the snapshot composition for Lidarr.

    Marked ``pinning`` because ``fetch_instance_snapshot`` is a new
    behavioural contract.
    """

    pytestmark = pytest.mark.pinning

    @pytest.mark.asyncio()
    async def test_paginated_walk_counts_future_anchors(self):
        future = "2999-01-01T00:00:00Z"
        client = AsyncMock(spec=LidarrClient)
        client.get_wanted_total.side_effect = lambda kind: {"missing": 3, "cutoff": 2}[kind]
        client.get_missing.return_value = [
            _make_album(album_id=1, release_date=_OLD_DATE),
            _make_album(album_id=2, release_date=future),
            _make_album(album_id=3, release_date=None),
        ]

        snap = await fetch_instance_snapshot(client, _make_instance())

        assert snap.monitored_total == 5
        assert snap.unreleased_count == 1

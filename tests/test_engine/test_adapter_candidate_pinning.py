"""Pin the SearchCandidate byte-shape produced by every adapter.

Track A.21 of the refactor plan.  Track C.7-C.9 will extract shared
adapt_missing / adapt_cutoff / fetch_upgrade_pool templates in
``engine/adapters/_common.py``.  These tests snapshot the exact
SearchCandidate each of the six adapters returns today for an
already-released item, so the template extraction cannot silently
drift item_id, item_type, label shape, unreleased_reason, group_key,
or search_payload.
"""

from __future__ import annotations

import pytest

from houndarr.clients.lidarr import MissingAlbum
from houndarr.clients.radarr import MissingMovie
from houndarr.clients.readarr import MissingBook
from houndarr.clients.sonarr import MissingEpisode
from houndarr.clients.whisparr_v2 import MissingWhisparrEpisode
from houndarr.clients.whisparr_v3 import MissingWhisparrV3Movie
from houndarr.engine.adapters.lidarr import (
    adapt_cutoff as lidarr_adapt_cutoff,
)
from houndarr.engine.adapters.lidarr import (
    adapt_missing as lidarr_adapt_missing,
)
from houndarr.engine.adapters.radarr import (
    adapt_cutoff as radarr_adapt_cutoff,
)
from houndarr.engine.adapters.radarr import (
    adapt_missing as radarr_adapt_missing,
)
from houndarr.engine.adapters.readarr import (
    adapt_cutoff as readarr_adapt_cutoff,
)
from houndarr.engine.adapters.readarr import (
    adapt_missing as readarr_adapt_missing,
)
from houndarr.engine.adapters.sonarr import (
    adapt_cutoff as sonarr_adapt_cutoff,
)
from houndarr.engine.adapters.sonarr import (
    adapt_missing as sonarr_adapt_missing,
)
from houndarr.engine.adapters.whisparr_v2 import (
    adapt_cutoff as whisparr_v2_adapt_cutoff,
)
from houndarr.engine.adapters.whisparr_v2 import (
    adapt_missing as whisparr_v2_adapt_missing,
)
from houndarr.engine.adapters.whisparr_v3 import (
    adapt_cutoff as whisparr_v3_adapt_cutoff,
)
from houndarr.engine.adapters.whisparr_v3 import (
    adapt_missing as whisparr_v3_adapt_missing,
)
from houndarr.services.instances import InstanceType, SonarrSearchMode
from tests.test_engine.conftest import make_instance

pytestmark = pytest.mark.pinning


_PAST_ISO = "2020-01-01T00:00:00Z"


def _radarr_movie() -> MissingMovie:
    return MissingMovie(
        movie_id=101,
        title="Classic Film",
        year=2020,
        status="released",
        minimum_availability="released",
        is_available=True,
        in_cinemas=_PAST_ISO,
        physical_release=_PAST_ISO,
        release_date=_PAST_ISO,
        digital_release=_PAST_ISO,
    )


def _sonarr_episode() -> MissingEpisode:
    return MissingEpisode(
        episode_id=55,
        series_id=7,
        series_title="My Show",
        episode_title="Pilot",
        season=1,
        episode=1,
        air_date_utc=_PAST_ISO,
    )


def _lidarr_album() -> MissingAlbum:
    return MissingAlbum(
        album_id=201,
        artist_id=11,
        artist_name="Test Artist",
        title="First Album",
        release_date=_PAST_ISO,
    )


def _readarr_book() -> MissingBook:
    return MissingBook(
        book_id=301,
        author_id=31,
        author_name="Test Author",
        title="First Book",
        release_date=_PAST_ISO,
    )


def _whisparr_v2_episode() -> MissingWhisparrEpisode:
    return MissingWhisparrEpisode(
        episode_id=401,
        series_id=41,
        series_title="Site",
        episode_title="Scene",
        season_number=1,
        absolute_episode_number=None,
        release_date=None,  # datetime | None
    )


def _whisparr_v3_movie() -> MissingWhisparrV3Movie:
    return MissingWhisparrV3Movie(
        movie_id=501,
        title="V3 Movie",
        year=2021,
        status="released",
        minimum_availability="released",
        is_available=True,
        in_cinemas=_PAST_ISO,
        physical_release=_PAST_ISO,
        release_date=_PAST_ISO,
        digital_release=_PAST_ISO,
    )


# ---------------------------------------------------------------------------
# Radarr
# ---------------------------------------------------------------------------


class TestRadarrAdapter:
    def test_adapt_missing_candidate_shape(self) -> None:
        inst = make_instance(itype=InstanceType.radarr)
        cand = radarr_adapt_missing(_radarr_movie(), inst)
        assert cand.item_id == 101
        assert cand.item_type == "movie"
        assert cand.label == "Classic Film (2020)"
        assert cand.unreleased_reason is None
        assert cand.group_key is None
        assert cand.search_payload == {"command": "MoviesSearch", "movie_id": 101}

    def test_adapt_cutoff_matches_missing(self) -> None:
        inst = make_instance(itype=InstanceType.radarr)
        movie = _radarr_movie()
        assert radarr_adapt_cutoff(movie, inst) == radarr_adapt_missing(movie, inst)


# ---------------------------------------------------------------------------
# Sonarr
# ---------------------------------------------------------------------------


class TestSonarrAdapter:
    def test_adapt_missing_episode_mode(self) -> None:
        inst = make_instance(itype=InstanceType.sonarr, sonarr_search_mode=SonarrSearchMode.episode)
        cand = sonarr_adapt_missing(_sonarr_episode(), inst)
        assert cand.item_id == 55
        assert cand.item_type == "episode"
        assert cand.group_key is None
        assert cand.search_payload["command"] == "EpisodeSearch"
        assert cand.search_payload["episode_id"] == 55

    def test_adapt_missing_season_context_mode(self) -> None:
        inst = make_instance(
            itype=InstanceType.sonarr,
            sonarr_search_mode=SonarrSearchMode.season_context,
        )
        cand = sonarr_adapt_missing(_sonarr_episode(), inst)
        assert cand.group_key == (7, 1)
        assert cand.search_payload["command"] == "SeasonSearch"
        assert cand.search_payload["series_id"] == 7
        assert cand.search_payload["season_number"] == 1

    def test_adapt_cutoff_always_episode_mode(self) -> None:
        inst = make_instance(
            itype=InstanceType.sonarr,
            sonarr_search_mode=SonarrSearchMode.season_context,
        )
        cand = sonarr_adapt_cutoff(_sonarr_episode(), inst)
        assert cand.item_type == "episode"
        assert cand.group_key is None
        assert cand.search_payload["command"] == "EpisodeSearch"


# ---------------------------------------------------------------------------
# Lidarr
# ---------------------------------------------------------------------------


class TestLidarrAdapter:
    def test_adapt_missing_album_shape(self) -> None:
        inst = make_instance(itype=InstanceType.lidarr)
        cand = lidarr_adapt_missing(_lidarr_album(), inst)
        assert cand.item_id == 201
        assert cand.item_type == "album"
        assert cand.search_payload["command"] == "AlbumSearch"

    def test_adapt_cutoff_album_type(self) -> None:
        inst = make_instance(itype=InstanceType.lidarr)
        cand = lidarr_adapt_cutoff(_lidarr_album(), inst)
        assert cand.item_type == "album"


# ---------------------------------------------------------------------------
# Readarr
# ---------------------------------------------------------------------------


class TestReadarrAdapter:
    def test_adapt_missing_book_shape(self) -> None:
        inst = make_instance(itype=InstanceType.readarr)
        cand = readarr_adapt_missing(_readarr_book(), inst)
        assert cand.item_id == 301
        assert cand.item_type == "book"
        assert cand.search_payload["command"] == "BookSearch"

    def test_adapt_cutoff_book_type(self) -> None:
        inst = make_instance(itype=InstanceType.readarr)
        cand = readarr_adapt_cutoff(_readarr_book(), inst)
        assert cand.item_type == "book"


# ---------------------------------------------------------------------------
# Whisparr v2
# ---------------------------------------------------------------------------


class TestWhisparrV2Adapter:
    def test_adapt_missing_episode_shape(self) -> None:
        inst = make_instance(itype=InstanceType.whisparr_v2)
        cand = whisparr_v2_adapt_missing(_whisparr_v2_episode(), inst)
        assert cand.item_id == 401
        assert cand.item_type == "whisparr_episode"
        assert cand.search_payload["command"] == "EpisodeSearch"

    def test_adapt_cutoff_episode_type(self) -> None:
        inst = make_instance(itype=InstanceType.whisparr_v2)
        cand = whisparr_v2_adapt_cutoff(_whisparr_v2_episode(), inst)
        assert cand.item_type == "whisparr_episode"


# ---------------------------------------------------------------------------
# Whisparr v3
# ---------------------------------------------------------------------------


class TestWhisparrV3Adapter:
    def test_adapt_missing_movie_shape(self) -> None:
        inst = make_instance(itype=InstanceType.whisparr_v3)
        cand = whisparr_v3_adapt_missing(_whisparr_v3_movie(), inst)
        assert cand.item_id == 501
        assert cand.item_type == "whisparr_v3_movie"
        assert cand.search_payload["command"] == "MoviesSearch"

    def test_adapt_cutoff_matches_missing(self) -> None:
        inst = make_instance(itype=InstanceType.whisparr_v3)
        movie = _whisparr_v3_movie()
        assert whisparr_v3_adapt_cutoff(movie, inst).item_type == "whisparr_v3_movie"

"""Pin ``SearchCandidate.leaf_id``: set only when ``item_id`` is a synthetic parent id.

The engine's download-queue check (issue #765) matches context-mode
candidates on ``leaf_id`` because their ``item_id`` is a negative synthetic
season / artist / author id that never appears in the *arr queue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from houndarr.clients.lidarr import LibraryAlbum, MissingAlbum
from houndarr.clients.radarr import LibraryMovie, MissingMovie
from houndarr.clients.readarr import LibraryBook, MissingBook
from houndarr.clients.sonarr import LibraryEpisode, MissingEpisode
from houndarr.clients.whisparr_v2 import LibraryWhisparrV2Episode, MissingWhisparrV2Episode
from houndarr.engine.adapters import lidarr, radarr, readarr, sonarr, whisparr_v2
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrV2SearchMode,
)
from tests.test_engine.conftest import make_instance

_OLD = "2020-01-01T00:00:00Z"

_MISSING_EPISODE = MissingEpisode(
    episode_id=101,
    series_id=55,
    series_title="Show",
    episode_title="Pilot",
    season=2,
    episode=1,
    air_date_utc=_OLD,
)
_LIBRARY_EPISODE = LibraryEpisode(
    episode_id=102,
    series_id=55,
    series_title="Show",
    episode_title="Second",
    season=2,
    episode=2,
    monitored=True,
    has_file=True,
    cutoff_met=True,
)
_MISSING_ALBUM = MissingAlbum(
    album_id=301, artist_id=50, artist_name="Artist", title="First", release_date=_OLD
)
_LIBRARY_ALBUM = LibraryAlbum(
    album_id=302,
    artist_id=50,
    artist_name="Artist",
    title="Second",
    monitored=True,
    has_file=True,
    release_date=_OLD,
)
_MISSING_BOOK = MissingBook(
    book_id=401, author_id=60, author_name="Author", title="First", release_date=_OLD
)
_LIBRARY_BOOK = LibraryBook(
    book_id=402,
    author_id=60,
    author_name="Author",
    title="Second",
    monitored=True,
    has_file=True,
    release_date=_OLD,
)
_MISSING_SCENE = MissingWhisparrV2Episode(
    episode_id=501,
    series_id=70,
    series_title="Site",
    episode_title="Scene",
    season_number=2023,
    absolute_episode_number=5,
    release_date=datetime(2023, 9, 1, tzinfo=UTC),
)
_LIBRARY_SCENE = LibraryWhisparrV2Episode(
    episode_id=502,
    series_id=70,
    series_title="Site",
    episode_title="Scene 2",
    season_number=2023,
    absolute_episode_number=6,
    monitored=True,
    has_file=True,
    cutoff_met=True,
)

# (adapter, instance type, item-mode overrides, context-mode overrides)
_CONTEXT_ADAPTERS: list[tuple[Any, InstanceType, dict[str, Any], dict[str, Any]]] = [
    (
        sonarr,
        InstanceType.sonarr,
        {"sonarr_search_mode": SonarrSearchMode.episode},
        {
            "sonarr_search_mode": SonarrSearchMode.season_context,
            "upgrade_sonarr_search_mode": SonarrSearchMode.season_context,
        },
    ),
    (
        whisparr_v2,
        InstanceType.whisparr_v2,
        {"whisparr_v2_search_mode": WhisparrV2SearchMode.episode},
        {
            "whisparr_v2_search_mode": WhisparrV2SearchMode.season_context,
            "upgrade_whisparr_v2_search_mode": WhisparrV2SearchMode.season_context,
        },
    ),
    (
        lidarr,
        InstanceType.lidarr,
        {"lidarr_search_mode": LidarrSearchMode.album},
        {
            "lidarr_search_mode": LidarrSearchMode.artist_context,
            "upgrade_lidarr_search_mode": LidarrSearchMode.artist_context,
        },
    ),
    (
        readarr,
        InstanceType.readarr,
        {"readarr_search_mode": ReadarrSearchMode.book},
        {
            "readarr_search_mode": ReadarrSearchMode.author_context,
            "upgrade_readarr_search_mode": ReadarrSearchMode.author_context,
        },
    ),
]

_ITEMS: dict[Any, tuple[Any, int, Any, int]] = {
    sonarr: (_MISSING_EPISODE, 101, _LIBRARY_EPISODE, 102),
    whisparr_v2: (_MISSING_SCENE, 501, _LIBRARY_SCENE, 502),
    lidarr: (_MISSING_ALBUM, 301, _LIBRARY_ALBUM, 302),
    readarr: (_MISSING_BOOK, 401, _LIBRARY_BOOK, 402),
}


@pytest.mark.parametrize(("adapter", "itype", "item_mode", "context_mode"), _CONTEXT_ADAPTERS)
def test_context_mode_candidates_carry_the_wanted_record_id(
    adapter: Any,
    itype: InstanceType,
    item_mode: dict[str, Any],
    context_mode: dict[str, Any],
) -> None:
    missing, missing_id, library, library_id = _ITEMS[adapter]
    instance = make_instance(itype=itype, **context_mode)

    missing_candidate = adapter.adapt_missing(missing, instance)
    upgrade_candidate = adapter.adapt_upgrade(library, instance)

    assert missing_candidate.group_key is not None
    assert missing_candidate.item_id < 0
    assert missing_candidate.leaf_id == missing_id
    assert upgrade_candidate.group_key is not None
    assert upgrade_candidate.item_id < 0
    assert upgrade_candidate.leaf_id == library_id


@pytest.mark.parametrize(("adapter", "itype", "item_mode", "context_mode"), _CONTEXT_ADAPTERS)
def test_item_mode_and_cutoff_candidates_leave_leaf_id_unset(
    adapter: Any,
    itype: InstanceType,
    item_mode: dict[str, Any],
    context_mode: dict[str, Any],
) -> None:
    missing, missing_id, library, library_id = _ITEMS[adapter]
    item_instance = make_instance(itype=itype, **item_mode)
    context_instance = make_instance(itype=itype, **context_mode)

    assert adapter.adapt_missing(missing, item_instance).leaf_id is None
    assert adapter.adapt_missing(missing, item_instance).item_id == missing_id
    assert adapter.adapt_upgrade(library, item_instance).leaf_id is None
    assert adapter.adapt_upgrade(library, item_instance).item_id == library_id
    # Cutoff always dispatches per item, even when missing runs in context mode.
    assert adapter.adapt_cutoff(missing, context_instance).leaf_id is None
    assert adapter.adapt_cutoff(missing, context_instance).item_id == missing_id


def test_radarr_candidates_are_always_item_level() -> None:
    instance = make_instance(itype=InstanceType.radarr)
    movie = MissingMovie(
        movie_id=201,
        title="Movie",
        year=2023,
        status="released",
        minimum_availability="released",
        is_available=True,
        in_cinemas=_OLD,
        physical_release=_OLD,
        release_date=_OLD,
        digital_release=None,
    )
    library = LibraryMovie(
        movie_id=202,
        title="Movie 2",
        year=2023,
        monitored=True,
        has_file=True,
        cutoff_met=True,
        in_cinemas=_OLD,
        physical_release=_OLD,
        digital_release=None,
    )

    assert radarr.adapt_missing(movie, instance).leaf_id is None
    assert radarr.adapt_cutoff(movie, instance).leaf_id is None
    assert radarr.adapt_upgrade(library, instance).leaf_id is None

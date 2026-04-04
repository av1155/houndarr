"""Tests for adapter edge cases: adapt_missing, adapt_cutoff, adapt_upgrade per app."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from houndarr.clients.lidarr import LibraryAlbum, MissingAlbum
from houndarr.clients.radarr import LibraryMovie, MissingMovie
from houndarr.clients.readarr import LibraryBook, MissingBook
from houndarr.clients.sonarr import LibraryEpisode, MissingEpisode
from houndarr.clients.whisparr_v2 import LibraryWhisparrEpisode, MissingWhisparrEpisode
from houndarr.engine.adapters import lidarr as lidarr_adapter
from houndarr.engine.adapters import radarr as radarr_adapter
from houndarr.engine.adapters import readarr as readarr_adapter
from houndarr.engine.adapters import sonarr as sonarr_adapter
from houndarr.engine.adapters import whisparr_v2 as whisparr_adapter
from houndarr.engine.adapters.lidarr import _artist_item_id
from houndarr.engine.adapters.readarr import _author_item_id
from houndarr.engine.adapters.sonarr import _season_item_id
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrSearchMode,
)
from tests.test_engine.conftest import make_instance

# ---------------------------------------------------------------------------
# Shared test data builders
# ---------------------------------------------------------------------------

_PAST_DATE = "2020-01-01T00:00:00Z"


def _missing_episode(
    *,
    episode_id: int = 101,
    series_id: int | None = 55,
    season: int = 1,
    episode: int = 1,
    air_date_utc: str | None = _PAST_DATE,
) -> MissingEpisode:
    return MissingEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title="My Show",
        episode_title="Pilot",
        season=season,
        episode=episode,
        air_date_utc=air_date_utc,
    )


def _library_episode(
    *,
    episode_id: int = 101,
    series_id: int = 55,
    season: int = 1,
    episode: int = 1,
) -> LibraryEpisode:
    return LibraryEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title="My Show",
        episode_title="Pilot",
        season=season,
        episode=episode,
        monitored=True,
        has_file=True,
        cutoff_met=True,
    )


def _missing_movie(*, movie_id: int = 201) -> MissingMovie:
    return MissingMovie(
        movie_id=movie_id,
        title="My Movie",
        year=2023,
        status="released",
        minimum_availability="released",
        is_available=True,
        in_cinemas="2023-01-01T00:00:00Z",
        physical_release="2023-02-01T00:00:00Z",
        release_date="2023-02-01T00:00:00Z",
        digital_release=None,
    )


def _library_movie(*, movie_id: int = 201) -> LibraryMovie:
    return LibraryMovie(
        movie_id=movie_id,
        title="My Movie",
        year=2023,
        monitored=True,
        has_file=True,
        cutoff_met=True,
        in_cinemas="2023-01-01T00:00:00Z",
        physical_release=None,
        digital_release=None,
    )


def _missing_album(
    *,
    album_id: int = 301,
    artist_id: int = 50,
) -> MissingAlbum:
    return MissingAlbum(
        album_id=album_id,
        artist_id=artist_id,
        artist_name="Test Artist",
        title="Greatest Hits",
        release_date=_PAST_DATE,
    )


def _library_album(
    *,
    album_id: int = 301,
    artist_id: int = 50,
) -> LibraryAlbum:
    return LibraryAlbum(
        album_id=album_id,
        artist_id=artist_id,
        artist_name="Test Artist",
        title="Greatest Hits",
        monitored=True,
        has_file=True,
        release_date=_PAST_DATE,
    )


def _missing_book(
    *,
    book_id: int = 401,
    author_id: int = 60,
) -> MissingBook:
    return MissingBook(
        book_id=book_id,
        author_id=author_id,
        author_name="Test Author",
        title="Test Book",
        release_date=_PAST_DATE,
    )


def _library_book(
    *,
    book_id: int = 401,
    author_id: int = 60,
) -> LibraryBook:
    return LibraryBook(
        book_id=book_id,
        author_id=author_id,
        author_name="Test Author",
        title="Test Book",
        monitored=True,
        has_file=True,
        release_date=_PAST_DATE,
    )


def _missing_whisparr_episode(
    *,
    episode_id: int = 501,
    series_id: int | None = 70,
    season_number: int = 1,
    release_date: datetime | None = None,
) -> MissingWhisparrEpisode:
    return MissingWhisparrEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title="My Whisparr Show",
        episode_title="Scene Title",
        season_number=season_number,
        absolute_episode_number=5,
        release_date=release_date,
    )


def _library_whisparr_episode(
    *,
    episode_id: int = 501,
    series_id: int = 70,
    season_number: int = 1,
) -> LibraryWhisparrEpisode:
    return LibraryWhisparrEpisode(
        episode_id=episode_id,
        series_id=series_id,
        series_title="My Whisparr Show",
        episode_title="Scene Title",
        season_number=season_number,
        absolute_episode_number=5,
        monitored=True,
        has_file=True,
        cutoff_met=True,
    )


# ---------------------------------------------------------------------------
# Sonarr adapter
# ---------------------------------------------------------------------------


def test_sonarr_adapt_missing_episode_mode() -> None:
    """Episode mode: item_id=episode_id, group_key=None."""
    inst = make_instance(itype=InstanceType.sonarr, sonarr_search_mode=SonarrSearchMode.episode)
    item = _missing_episode()
    cand = sonarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 101
    assert cand.item_type == "episode"
    assert cand.group_key is None
    assert cand.search_payload["command"] == "EpisodeSearch"
    assert cand.search_payload["episode_id"] == 101


def test_sonarr_adapt_missing_season_context() -> None:
    """Season-context mode: synthetic item_id, group_key=(series_id, season)."""
    inst = make_instance(
        itype=InstanceType.sonarr,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    item = _missing_episode(series_id=55, season=2)
    cand = sonarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == _season_item_id(55, 2)
    assert cand.group_key == (55, 2)
    assert cand.search_payload["command"] == "SeasonSearch"


def test_sonarr_adapt_missing_season_zero_falls_back_to_episode() -> None:
    """Season 0 (specials) in season_context mode falls back to episode mode."""
    inst = make_instance(
        itype=InstanceType.sonarr,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    item = _missing_episode(series_id=55, season=0)
    cand = sonarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 101
    assert cand.group_key is None
    assert cand.search_payload["command"] == "EpisodeSearch"


def test_sonarr_adapt_missing_series_id_none_falls_back() -> None:
    """series_id=None in season_context mode falls back to episode mode."""
    inst = make_instance(
        itype=InstanceType.sonarr,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    item = _missing_episode(series_id=None, season=1)
    cand = sonarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 101
    assert cand.group_key is None


def test_sonarr_adapt_cutoff_always_episode_mode() -> None:
    """Cutoff pass always uses episode mode, regardless of sonarr_search_mode."""
    inst = make_instance(
        itype=InstanceType.sonarr,
        sonarr_search_mode=SonarrSearchMode.episode,
    )
    item = _missing_episode()
    cand = sonarr_adapter.adapt_cutoff(item, inst)

    assert cand.item_id == 101
    assert cand.group_key is None
    assert cand.search_payload["command"] == "EpisodeSearch"


def test_sonarr_adapt_cutoff_season_context_still_episode() -> None:
    """Even with season_context setting, cutoff uses episode mode."""
    inst = make_instance(
        itype=InstanceType.sonarr,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    item = _missing_episode(series_id=55, season=2)
    cand = sonarr_adapter.adapt_cutoff(item, inst)

    assert cand.item_id == 101
    assert cand.group_key is None
    assert cand.search_payload["command"] == "EpisodeSearch"


# ---------------------------------------------------------------------------
# Radarr adapter
# ---------------------------------------------------------------------------


def test_radarr_adapt_missing_movie_payload() -> None:
    """Radarr missing: command=MoviesSearch, group_key=None."""
    inst = make_instance(itype=InstanceType.radarr)
    item = _missing_movie()
    cand = radarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 201
    assert cand.item_type == "movie"
    assert cand.group_key is None
    assert cand.search_payload["command"] == "MoviesSearch"
    assert cand.search_payload["movie_id"] == 201


def test_radarr_adapt_cutoff_delegates_to_missing() -> None:
    """Radarr adapt_cutoff produces the same result as adapt_missing."""
    inst = make_instance(itype=InstanceType.radarr)
    item = _missing_movie()
    missing_cand = radarr_adapter.adapt_missing(item, inst)
    cutoff_cand = radarr_adapter.adapt_cutoff(item, inst)

    assert missing_cand.item_id == cutoff_cand.item_id
    assert missing_cand.search_payload == cutoff_cand.search_payload
    assert missing_cand.group_key == cutoff_cand.group_key


def test_radarr_adapt_upgrade_no_unreleased_reason() -> None:
    """Radarr upgrade candidates always have unreleased_reason=None."""
    inst = make_instance(itype=InstanceType.radarr)
    item = _library_movie()
    cand = radarr_adapter.adapt_upgrade(item, inst)

    assert cand.unreleased_reason is None
    assert cand.search_payload["command"] == "MoviesSearch"


# ---------------------------------------------------------------------------
# Lidarr adapter
# ---------------------------------------------------------------------------


def test_lidarr_adapt_missing_album_mode() -> None:
    """Album mode: album_id, group_key=None, command=AlbumSearch."""
    inst = make_instance(
        itype=InstanceType.lidarr,
        lidarr_search_mode=LidarrSearchMode.album,
    )
    item = _missing_album()
    cand = lidarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 301
    assert cand.item_type == "album"
    assert cand.group_key is None
    assert cand.search_payload["command"] == "AlbumSearch"


def test_lidarr_adapt_missing_artist_context() -> None:
    """Artist-context: synthetic_id, group_key=(artist_id, 0), command=ArtistSearch."""
    inst = make_instance(
        itype=InstanceType.lidarr,
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    item = _missing_album(artist_id=50)
    cand = lidarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == _artist_item_id(50)
    assert cand.group_key == (50, 0)
    assert cand.search_payload["command"] == "ArtistSearch"


def test_lidarr_adapt_missing_artist_id_zero_fallback() -> None:
    """artist_id=0 in artist_context mode falls back to album mode."""
    inst = make_instance(
        itype=InstanceType.lidarr,
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    item = _missing_album(artist_id=0)
    cand = lidarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 301
    assert cand.group_key is None
    assert cand.search_payload["command"] == "AlbumSearch"


def test_lidarr_adapt_cutoff_always_album_mode() -> None:
    """Cutoff always uses album mode regardless of lidarr_search_mode."""
    inst = make_instance(
        itype=InstanceType.lidarr,
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    item = _missing_album(artist_id=50)
    cand = lidarr_adapter.adapt_cutoff(item, inst)

    assert cand.item_id == 301
    assert cand.group_key is None
    assert cand.search_payload["command"] == "AlbumSearch"


def test_lidarr_adapt_upgrade_artist_context() -> None:
    """Upgrade with upgrade_lidarr_search_mode=artist_context."""
    inst = make_instance(
        itype=InstanceType.lidarr,
        upgrade_lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    item = _library_album(artist_id=50)
    cand = lidarr_adapter.adapt_upgrade(item, inst)

    assert cand.item_id == _artist_item_id(50)
    assert cand.group_key == (50, 0)
    assert cand.unreleased_reason is None


# ---------------------------------------------------------------------------
# Readarr adapter
# ---------------------------------------------------------------------------


def test_readarr_adapt_missing_book_mode() -> None:
    """Book mode: book_id, group_key=None, command=BookSearch."""
    inst = make_instance(
        itype=InstanceType.readarr,
        readarr_search_mode=ReadarrSearchMode.book,
    )
    item = _missing_book()
    cand = readarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 401
    assert cand.item_type == "book"
    assert cand.group_key is None
    assert cand.search_payload["command"] == "BookSearch"


def test_readarr_adapt_missing_author_context() -> None:
    """Author-context: synthetic_id, group_key=(author_id, 0), command=AuthorSearch."""
    inst = make_instance(
        itype=InstanceType.readarr,
        readarr_search_mode=ReadarrSearchMode.author_context,
    )
    item = _missing_book(author_id=60)
    cand = readarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == _author_item_id(60)
    assert cand.group_key == (60, 0)
    assert cand.search_payload["command"] == "AuthorSearch"


def test_readarr_adapt_missing_author_id_zero_fallback() -> None:
    """author_id=0 in author_context mode falls back to book mode."""
    inst = make_instance(
        itype=InstanceType.readarr,
        readarr_search_mode=ReadarrSearchMode.author_context,
    )
    item = _missing_book(author_id=0)
    cand = readarr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 401
    assert cand.group_key is None
    assert cand.search_payload["command"] == "BookSearch"


def test_readarr_adapt_cutoff_always_book_mode() -> None:
    """Cutoff always uses book mode regardless of readarr_search_mode."""
    inst = make_instance(
        itype=InstanceType.readarr,
        readarr_search_mode=ReadarrSearchMode.author_context,
    )
    item = _missing_book(author_id=60)
    cand = readarr_adapter.adapt_cutoff(item, inst)

    assert cand.item_id == 401
    assert cand.group_key is None
    assert cand.search_payload["command"] == "BookSearch"


def test_readarr_adapt_upgrade_author_context() -> None:
    """Upgrade with upgrade_readarr_search_mode=author_context."""
    inst = make_instance(
        itype=InstanceType.readarr,
        upgrade_readarr_search_mode=ReadarrSearchMode.author_context,
    )
    item = _library_book(author_id=60)
    cand = readarr_adapter.adapt_upgrade(item, inst)

    assert cand.item_id == _author_item_id(60)
    assert cand.group_key == (60, 0)
    assert cand.unreleased_reason is None


# ---------------------------------------------------------------------------
# Whisparr adapter
# ---------------------------------------------------------------------------


def test_whisparr_adapt_missing_episode_mode() -> None:
    """Episode mode: item_type=whisparr_episode, group_key=None."""
    inst = make_instance(
        itype=InstanceType.whisparr_v2,
        whisparr_search_mode=WhisparrSearchMode.episode,
    )
    item = _missing_whisparr_episode()
    cand = whisparr_adapter.adapt_missing(item, inst)

    assert cand.item_id == 501
    assert cand.item_type == "whisparr_episode"
    assert cand.group_key is None
    assert cand.search_payload["command"] == "EpisodeSearch"


def test_whisparr_adapt_missing_season_context() -> None:
    """Season-context: synthetic item_id, group_key=(series_id, season)."""
    inst = make_instance(
        itype=InstanceType.whisparr_v2,
        whisparr_search_mode=WhisparrSearchMode.season_context,
    )
    item = _missing_whisparr_episode(series_id=70, season_number=2)
    cand = whisparr_adapter.adapt_missing(item, inst)

    expected_id = -(70 * 1000 + 2)
    assert cand.item_id == expected_id
    assert cand.group_key == (70, 2)
    assert cand.search_payload["command"] == "SeasonSearch"


def test_whisparr_adapt_cutoff_always_episode_mode() -> None:
    """Cutoff always uses episode mode for Whisparr."""
    inst = make_instance(
        itype=InstanceType.whisparr_v2,
        whisparr_search_mode=WhisparrSearchMode.season_context,
    )
    item = _missing_whisparr_episode(series_id=70, season_number=2)
    cand = whisparr_adapter.adapt_cutoff(item, inst)

    assert cand.item_id == 501
    assert cand.group_key is None
    assert cand.search_payload["command"] == "EpisodeSearch"


def test_whisparr_adapt_upgrade_season_context() -> None:
    """Upgrade with upgrade_whisparr_search_mode=season_context."""
    inst = make_instance(
        itype=InstanceType.whisparr_v2,
        upgrade_whisparr_search_mode=WhisparrSearchMode.season_context,
    )
    item = _library_whisparr_episode(series_id=70, season_number=2)
    cand = whisparr_adapter.adapt_upgrade(item, inst)

    expected_id = -(70 * 1000 + 2)
    assert cand.item_id == expected_id
    assert cand.group_key == (70, 2)
    assert cand.unreleased_reason is None


def test_whisparr_unreleased_with_future_datetime() -> None:
    """A future datetime produces unreleased_reason='not yet released'."""
    inst = make_instance(
        itype=InstanceType.whisparr_v2,
        whisparr_search_mode=WhisparrSearchMode.episode,
    )
    future_dt = datetime.now(UTC) + timedelta(days=30)
    item = _missing_whisparr_episode(release_date=future_dt)
    cand = whisparr_adapter.adapt_missing(item, inst)

    assert cand.unreleased_reason == "not yet released"


def test_whisparr_unreleased_with_none_release_date() -> None:
    """release_date=None means the item is eligible (unreleased_reason=None)."""
    inst = make_instance(
        itype=InstanceType.whisparr_v2,
        whisparr_search_mode=WhisparrSearchMode.episode,
    )
    item = _missing_whisparr_episode(release_date=None)
    cand = whisparr_adapter.adapt_missing(item, inst)

    assert cand.unreleased_reason is None

"""Pydantic wire models for *arr API responses.

Every response the clients parse goes through one of these models first.
Pydantic validates the JSON shape with readable errors when an *arr
drifts (field removed, type changed, null where a string was expected)
instead of the KeyError an ad hoc ``dict.get`` chain would produce
deep inside an adapter.

Field names are snake_case in Python; ``Field(alias="camelCase")`` maps
to the camelCase names the APIs serialise on the wire.  All models
share :class:`_ArrModel` which sets ``populate_by_name=True`` (so both
the alias and the Python name parse) and ``extra="ignore"`` (so the
many unused fields each *arr ships do not raise).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArrArtist",
    "ArrAuthor",
    "ArrSeries",
    "LidarrLibraryAlbum",
    "LidarrWantedAlbum",
    "PaginatedResponse",
    "QueueStatus",
    "RadarrLibraryMovie",
    "RadarrWantedMovie",
    "ReadarrLibraryBook",
    "ReadarrWantedBook",
    "SonarrLibraryEpisode",
    "SonarrWantedEpisode",
    "SystemStatus",
    "WhisparrV2LibraryEpisode",
    "WhisparrV2WantedEpisode",
    "WhisparrV3LibraryMovie",
]


# ---------------------------------------------------------------------------
# Base config
# ---------------------------------------------------------------------------


class _ArrModel(BaseModel):
    """Base for every wire model: tolerant of unknown fields, alias-driven."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class PaginatedResponse[T](_ArrModel):
    """Shared ``/wanted`` envelope across Sonarr, Radarr, Lidarr, Readarr, Whisparr v2.

    Whisparr v3 does not expose ``/wanted`` endpoints; its client filters the
    cached ``/api/v3/movie`` payload in memory instead.
    """

    records: list[T]
    total_records: int = Field(alias="totalRecords")
    page: int
    page_size: int = Field(alias="pageSize")


class SystemStatus(_ArrModel):
    """Result of ``/system/status``; used by :meth:`ArrClient.ping` and the
    Test Connection flow on the Settings page.

    Both fields are optional because *arr forks (Bookshelf, Reading Glasses)
    sometimes omit ``appName`` or ``version`` from their status payload and
    Houndarr must still report the instance as reachable.
    """

    app_name: str | None = Field(default=None, alias="appName")
    version: str | None = None


class QueueStatus(_ArrModel):
    """Result of ``/queue/status``.  ``total_count`` drives the supervisor's
    queue-backpressure gate: when it reaches an instance's ``queue_limit``
    the cycle skips dispatch.
    """

    total_count: int = Field(alias="totalCount")


# ---------------------------------------------------------------------------
# Shared parent-aggregate references
#
# Sonarr and Whisparr v2 episodes embed a ``series`` object; Lidarr albums
# embed ``artist``; Readarr books embed ``author``.  The same shapes are
# also returned as list items by ``get_series`` / ``get_artists`` /
# ``get_authors`` so adapters can filter by ``monitored``.
# ---------------------------------------------------------------------------


class ArrSeries(_ArrModel):
    id: int | None = None
    title: str | None = None
    monitored: bool | None = None


class ArrArtist(_ArrModel):
    id: int | None = None
    artist_name: str | None = Field(default=None, alias="artistName")
    monitored: bool | None = None


class ArrAuthor(_ArrModel):
    id: int | None = None
    author_name: str | None = Field(default=None, alias="authorName")
    monitored: bool | None = None


# ---------------------------------------------------------------------------
# File and statistics nested objects
# ---------------------------------------------------------------------------


class _WireEpisodeFile(_ArrModel):
    quality_cutoff_not_met: bool | None = Field(default=None, alias="qualityCutoffNotMet")


class _WireMovieFile(_ArrModel):
    quality_cutoff_not_met: bool | None = Field(default=None, alias="qualityCutoffNotMet")


class _WireAlbumStatistics(_ArrModel):
    track_file_count: int | None = Field(default=None, alias="trackFileCount")


class _WireBookStatistics(_ArrModel):
    book_file_count: int | None = Field(default=None, alias="bookFileCount")


# ---------------------------------------------------------------------------
# Per-app /wanted record models
# ---------------------------------------------------------------------------


class SonarrWantedEpisode(_ArrModel):
    id: int
    series_id: int | None = Field(default=None, alias="seriesId")
    series: ArrSeries | None = None
    series_title: str | None = Field(default=None, alias="seriesTitle")
    title: str | None = None
    season_number: int | None = Field(default=None, alias="seasonNumber")
    episode_number: int | None = Field(default=None, alias="episodeNumber")
    air_date_utc: str | None = Field(default=None, alias="airDateUtc")


class RadarrWantedMovie(_ArrModel):
    id: int
    title: str | None = None
    year: int | None = None
    status: str | None = None
    minimum_availability: str | None = Field(default=None, alias="minimumAvailability")
    is_available: bool | None = Field(default=None, alias="isAvailable")
    in_cinemas: str | None = Field(default=None, alias="inCinemas")
    physical_release: str | None = Field(default=None, alias="physicalRelease")
    release_date: str | None = Field(default=None, alias="releaseDate")
    digital_release: str | None = Field(default=None, alias="digitalRelease")


class LidarrWantedAlbum(_ArrModel):
    id: int
    artist_id: int | None = Field(default=None, alias="artistId")
    artist: ArrArtist | None = None
    title: str | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")


class ReadarrWantedBook(_ArrModel):
    id: int
    author_id: int | None = Field(default=None, alias="authorId")
    author: ArrAuthor | None = None
    title: str | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")


class WhisparrV2WantedEpisode(_ArrModel):
    """Whisparr v2 shares Sonarr's shape but reports ``releaseDate`` as either
    an ISO date string or a ``{year, month, day}`` object depending on the
    endpoint variant.  The domain parser normalises both into a ``datetime``.
    """

    id: int
    series_id: int | None = Field(default=None, alias="seriesId")
    series: ArrSeries | None = None
    series_title: str | None = Field(default=None, alias="seriesTitle")
    title: str | None = None
    season_number: int | None = Field(default=None, alias="seasonNumber")
    absolute_episode_number: int | None = Field(default=None, alias="absoluteEpisodeNumber")
    release_date: str | dict[str, int] | None = Field(default=None, alias="releaseDate")


# ---------------------------------------------------------------------------
# Per-app library record models
# ---------------------------------------------------------------------------


class SonarrLibraryEpisode(_ArrModel):
    id: int
    series_id: int | None = Field(default=None, alias="seriesId")
    series: ArrSeries | None = None
    title: str | None = None
    season_number: int | None = Field(default=None, alias="seasonNumber")
    episode_number: int | None = Field(default=None, alias="episodeNumber")
    monitored: bool | None = None
    has_file: bool | None = Field(default=None, alias="hasFile")
    episode_file: _WireEpisodeFile | None = Field(default=None, alias="episodeFile")


class RadarrLibraryMovie(_ArrModel):
    id: int
    title: str | None = None
    year: int | None = None
    monitored: bool | None = None
    has_file: bool | None = Field(default=None, alias="hasFile")
    movie_file: _WireMovieFile | None = Field(default=None, alias="movieFile")
    in_cinemas: str | None = Field(default=None, alias="inCinemas")
    physical_release: str | None = Field(default=None, alias="physicalRelease")
    digital_release: str | None = Field(default=None, alias="digitalRelease")
    release_date: str | None = Field(default=None, alias="releaseDate")


class LidarrLibraryAlbum(_ArrModel):
    id: int
    artist_id: int | None = Field(default=None, alias="artistId")
    artist: ArrArtist | None = None
    title: str | None = None
    monitored: bool | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")
    statistics: _WireAlbumStatistics | None = None


class ReadarrLibraryBook(_ArrModel):
    id: int
    author_id: int | None = Field(default=None, alias="authorId")
    author: ArrAuthor | None = None
    title: str | None = None
    monitored: bool | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")
    statistics: _WireBookStatistics | None = None


class WhisparrV2LibraryEpisode(_ArrModel):
    id: int
    series_id: int | None = Field(default=None, alias="seriesId")
    series: ArrSeries | None = None
    title: str | None = None
    season_number: int | None = Field(default=None, alias="seasonNumber")
    absolute_episode_number: int | None = Field(default=None, alias="absoluteEpisodeNumber")
    monitored: bool | None = None
    has_file: bool | None = Field(default=None, alias="hasFile")
    episode_file: _WireEpisodeFile | None = Field(default=None, alias="episodeFile")


class WhisparrV3LibraryMovie(_ArrModel):
    """Whisparr v3 exposes only ``/api/v3/movie``; this model backs both
    ``get_library`` and the client-side missing / cutoff filters that
    replace the absent ``/wanted`` endpoints.
    """

    id: int
    title: str | None = None
    year: int | None = None
    status: str | None = None
    minimum_availability: str | None = Field(default=None, alias="minimumAvailability")
    is_available: bool | None = Field(default=None, alias="isAvailable")
    monitored: bool | None = None
    has_file: bool | None = Field(default=None, alias="hasFile")
    movie_file: _WireMovieFile | None = Field(default=None, alias="movieFile")
    in_cinemas: str | None = Field(default=None, alias="inCinemas")
    physical_release: str | None = Field(default=None, alias="physicalRelease")
    digital_release: str | None = Field(default=None, alias="digitalRelease")
    release_date: str | None = Field(default=None, alias="releaseDate")

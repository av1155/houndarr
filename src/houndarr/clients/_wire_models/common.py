"""Shared Pydantic wire models: base class, envelope, status, aggregates.

These are the fields every ``*arr`` family uses verbatim, so they live
apart from the per-app files.  Anything with a name starting with
``_Wire`` is a private helper only meant to be composed into a public
per-app model; ``Arr{Series,Artist,Author}`` are the parent-aggregate
types that several apps embed.

Every model extends :class:`_ArrModel` which sets
``populate_by_name=True`` (so both the Python name and the
``Field(alias=...)`` camelCase name parse) and ``extra="ignore"``
(so new unused fields from an ``*arr`` release never raise).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ArrModel(BaseModel):
    """Base for every wire model: tolerant of unknown fields, alias-driven."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class PaginatedResponse[T](_ArrModel):
    """Shared ``/wanted`` envelope across Sonarr, Radarr, Lidarr, Readarr, Whisparr v2.

    Whisparr v3 does not expose ``/wanted`` endpoints; its client filters the
    cached ``/api/v3/movie`` payload in memory instead.

    The envelope fields default to zero / one so responses that supply only
    ``records`` (seen in tests and in the wild from some misbehaving
    proxies) still validate.  Real *arr responses always include the full
    envelope; the defaults merely preserve the tolerance the old
    ``dict.get(..., 0)`` chain already had.
    """

    records: list[T]
    total_records: int = Field(default=0, alias="totalRecords")
    page: int = 1
    page_size: int = Field(default=0, alias="pageSize")


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

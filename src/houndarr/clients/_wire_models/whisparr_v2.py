"""Whisparr v2 wire models: /wanted episode records + library episode shape.

Whisparr v2 is Sonarr-based; the shape differs only in the
``releaseDate`` wire form (the v2 API returns either an ISO date
string or a ``{year, month, day}`` dict depending on the endpoint
variant; the domain parser normalises both to ``datetime``).
"""

from __future__ import annotations

from pydantic import Field

from houndarr.clients._wire_models.common import (
    ArrSeries,
    _ArrModel,
    _WireEpisodeFile,
)


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

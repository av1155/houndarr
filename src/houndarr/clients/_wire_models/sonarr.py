"""Sonarr wire models: /wanted episode records + library episode shape."""

from __future__ import annotations

from pydantic import Field

from houndarr.clients._wire_models.common import (
    ArrSeries,
    _ArrModel,
    _WireEpisodeFile,
)


class SonarrWantedEpisode(_ArrModel):
    id: int
    series_id: int | None = Field(default=None, alias="seriesId")
    series: ArrSeries | None = None
    series_title: str | None = Field(default=None, alias="seriesTitle")
    title: str | None = None
    season_number: int | None = Field(default=None, alias="seasonNumber")
    episode_number: int | None = Field(default=None, alias="episodeNumber")
    air_date_utc: str | None = Field(default=None, alias="airDateUtc")


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

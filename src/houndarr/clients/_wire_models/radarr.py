"""Radarr wire models: /wanted movie records + library movie shape."""

from __future__ import annotations

from pydantic import Field

from houndarr.clients._wire_models.common import _ArrModel, _WireMovieFile


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

"""Whisparr v3 wire models: library movie shape.

Whisparr v3 is Radarr-based but lacks the ``/wanted`` endpoints, so
its client filters the cached ``/api/v3/movie`` payload in memory.
There is only one wire model: the library movie row.
"""

from __future__ import annotations

from pydantic import Field

from houndarr.clients._wire_models.common import _ArrModel, _WireMovieFile


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

"""Lidarr wire models: /wanted album records + library album shape."""

from __future__ import annotations

from pydantic import Field

from houndarr.clients._wire_models.common import (
    ArrArtist,
    _ArrModel,
    _WireAlbumStatistics,
)


class LidarrWantedAlbum(_ArrModel):
    id: int
    artist_id: int | None = Field(default=None, alias="artistId")
    artist: ArrArtist | None = None
    title: str | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")


class LidarrLibraryAlbum(_ArrModel):
    id: int
    artist_id: int | None = Field(default=None, alias="artistId")
    artist: ArrArtist | None = None
    title: str | None = None
    monitored: bool | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")
    statistics: _WireAlbumStatistics | None = None

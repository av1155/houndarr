"""Lidarr v1 API client: missing albums and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from houndarr.clients._wire_models import (
    LidarrLibraryAlbum,
    LidarrWantedAlbum,
    PaginatedResponse,
)
from houndarr.clients.base import ArrClient

__all__ = ["LidarrClient", "LibraryAlbum", "MissingAlbum"]


@dataclass(frozen=True)
class LibraryAlbum:
    """An album from Lidarr's full library endpoint."""

    album_id: int
    artist_id: int
    artist_name: str
    title: str
    monitored: bool
    has_file: bool
    release_date: str | None


@dataclass(frozen=True)
class MissingAlbum:
    """A single missing album returned by Lidarr's wanted/missing endpoint."""

    album_id: int
    artist_id: int
    artist_name: str
    title: str
    release_date: str | None  # ISO-8601 nullable string


class LidarrClient(ArrClient):
    """Async client for the Lidarr v1 REST API."""

    _SYSTEM_STATUS_PATH: str = "/api/v1/system/status"
    _QUEUE_STATUS_PATH: str = "/api/v1/queue/status"

    async def get_missing(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingAlbum]:
        """Return a page of monitored missing albums.

        Calls ``GET /api/v1/wanted/missing`` with ``includeArtist=true``
        so that artist metadata is embedded in each record.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingAlbum` dataclasses.
        """
        data = await self._get(
            "/api/v1/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="releaseDate",
            sortDirection="ascending",
            includeArtist="true",
            monitored="true",
        )
        envelope = PaginatedResponse[LidarrWantedAlbum].model_validate(data)
        return [_parse_album(w) for w in envelope.records]

    async def search(self, item_id: int) -> None:
        """Trigger an automatic album search in Lidarr.

        Calls ``POST /api/v1/command`` with command ``AlbumSearch``.

        Args:
            item_id: Lidarr album ID to search for.
        """
        await self._post(
            "/api/v1/command",
            json={"name": "AlbumSearch", "albumIds": [item_id]},
        )

    async def search_artist(self, artist_id: int) -> None:
        """Trigger an artist-context search in Lidarr.

        Calls ``POST /api/v1/command`` with command ``ArtistSearch``.

        Args:
            artist_id: Lidarr artist ID.
        """
        await self._post(
            "/api/v1/command",
            json={"name": "ArtistSearch", "artistId": artist_id},
        )

    async def get_cutoff_unmet(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingAlbum]:
        """Return a page of monitored albums that have not met their quality cutoff.

        Calls ``GET /api/v1/wanted/cutoff`` with ``includeArtist=true``.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingAlbum` dataclasses.
        """
        data = await self._get(
            "/api/v1/wanted/cutoff",
            page=page,
            pageSize=page_size,
            includeArtist="true",
            monitored="true",
        )
        envelope = PaginatedResponse[LidarrWantedAlbum].model_validate(data)
        return [_parse_album(w) for w in envelope.records]

    async def get_wanted_total(self, kind: Literal["missing", "cutoff"]) -> int:
        """Return the totalRecords count for ``wanted/{kind}`` via a size-1 probe."""
        data = await self._get(
            f"/api/v1/wanted/{kind}",
            page=1,
            pageSize=1,
            sortKey="releaseDate",
            sortDirection="ascending",
            monitored="true",
        )
        envelope = PaginatedResponse[LidarrWantedAlbum].model_validate(data)
        return envelope.total_records

    async def get_albums(self) -> list[LibraryAlbum]:
        """Return the full album library.

        Calls ``GET /api/v1/album`` with ``includeArtist=true``.

        Returns:
            List of :class:`LibraryAlbum` dataclasses.
        """
        records = await self._get(
            "/api/v1/album",
            includeArtist="true",
        )
        return [_parse_library_album(LidarrLibraryAlbum.model_validate(r)) for r in records]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_library_album(wire: LidarrLibraryAlbum) -> LibraryAlbum:
    track_file_count = (
        wire.statistics.track_file_count or 0
        if wire.statistics is not None and wire.statistics.track_file_count is not None
        else 0
    )
    artist_id = wire.artist_id or (wire.artist.id if wire.artist else None) or 0
    artist_name = (wire.artist.artist_name if wire.artist else None) or ""
    return LibraryAlbum(
        album_id=wire.id,
        artist_id=artist_id,
        artist_name=artist_name,
        title=wire.title or "",
        monitored=bool(wire.monitored),
        has_file=track_file_count > 0,
        release_date=wire.release_date,
    )


def _parse_album(wire: LidarrWantedAlbum) -> MissingAlbum:
    artist_id = wire.artist_id or (wire.artist.id if wire.artist else None) or 0
    artist_name = (wire.artist.artist_name if wire.artist else None) or ""
    return MissingAlbum(
        album_id=wire.id,
        artist_id=artist_id,
        artist_name=artist_name,
        title=wire.title or "",
        release_date=wire.release_date,
    )

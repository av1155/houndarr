"""Lidarr v1 API client: missing albums and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from houndarr.clients._wire_models import (
    LidarrLibraryAlbum,
    LidarrWantedAlbum,
    PaginatedResponse,
)
from houndarr.clients.base import ArrClient, WantedKind

__all__ = ["LidarrClient", "LibraryAlbum", "MissingAlbum"]


@dataclass(frozen=True, slots=True)
class LibraryAlbum:
    """An album from Lidarr's full library endpoint."""

    album_id: int
    artist_id: int
    artist_name: str
    title: str
    monitored: bool
    has_file: bool
    release_date: str | None


@dataclass(frozen=True, slots=True)
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
    # Lidarr is a v1 API (Sonarr / Radarr / Whisparr v2 are v3); the
    # override routes the /wanted template at /api/v1/wanted/{kind}.
    _WANTED_BASE_PATH: ClassVar[str] = "/api/v1/wanted"
    _WANTED_SORT_KEY: ClassVar[str] = "releaseDate"
    _WANTED_INCLUDE_PARAM: ClassVar[str | None] = "includeArtist"
    _WANTED_ENVELOPE: ClassVar[type[PaginatedResponse[LidarrWantedAlbum]]] = PaginatedResponse[
        LidarrWantedAlbum
    ]

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
        envelope = await self._fetch_wanted_page("missing", page=page, page_size=page_size)
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
        Lidarr's cutoff endpoint historically omits the sort params, so
        the call passes ``include_sort=False`` to suppress them.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingAlbum` dataclasses.
        """
        envelope = await self._fetch_wanted_page(
            "cutoff",
            page=page,
            page_size=page_size,
            include_sort=False,
        )
        return [_parse_album(w) for w in envelope.records]

    async def get_wanted_total(self, kind: WantedKind) -> int:
        """Return the totalRecords count for ``wanted/{kind}`` via a size-1 probe.

        Delegates to :meth:`ArrClient._fetch_wanted_total`, which wraps
        raw ``httpx`` and ``pydantic`` failures in typed
        :class:`~houndarr.errors.ClientError` subclasses with the
        original exception preserved on ``__cause__``.

        Raises:
            ClientHTTPError: Non-2xx response.
            ClientTransportError: Transport failure (connect, timeout,
                malformed URL, etc.).
            ClientValidationError: Response shape did not match the
                paginated envelope schema.
        """
        return await self._fetch_wanted_total(kind)

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


# Parsing helpers


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

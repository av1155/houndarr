"""Lidarr v1 API client — missing albums and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from houndarr.clients.base import ArrClient

__all__ = ["LidarrClient", "MissingAlbum"]


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
        data: dict[str, Any] = await self._get(
            "/api/v1/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="releaseDate",
            sortDirection="ascending",
            includeArtist="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_album(r) for r in records]

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
        data: dict[str, Any] = await self._get(
            "/api/v1/wanted/cutoff",
            page=page,
            pageSize=page_size,
            includeArtist="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_album(r) for r in records]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_album(record: dict[str, Any]) -> MissingAlbum:
    artist: dict[str, Any] = record.get("artist") or {}
    return MissingAlbum(
        album_id=record["id"],
        artist_id=record.get("artistId") or artist.get("id") or 0,
        artist_name=artist.get("artistName") or "",
        title=record.get("title") or "",
        release_date=record.get("releaseDate"),
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_lidarr_client(
    url: str,
    api_key: str,
    timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
) -> LidarrClient:
    """Return a :class:`LidarrClient` ready for use as an async context manager."""
    return LidarrClient(url=url, api_key=api_key, timeout=timeout)

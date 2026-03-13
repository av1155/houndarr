"""Radarr v3 API client — missing movies and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from houndarr.clients.base import ArrClient

__all__ = ["MissingMovie", "RadarrClient"]


@dataclass(frozen=True)
class MissingMovie:
    """A single missing movie returned by Radarr's wanted/missing endpoint."""

    movie_id: int
    title: str
    year: int
    digital_release: str | None  # ISO-8601 date or None if unknown


class RadarrClient(ArrClient):
    """Async client for the Radarr v3 REST API."""

    async def get_missing(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingMovie]:
        """Return a page of monitored missing movies.

        Calls ``GET /api/v3/wanted/missing`` sorted by in-cinema date
        (oldest first) so higher-priority titles are processed first.

        Args:
            page: 1-based page number.
            page_size: Number of records per page (max 250 in Radarr).

        Returns:
            List of :class:`MissingMovie` dataclasses.
        """
        data: dict[str, Any] = await self._get(
            "/api/v3/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="inCinemas",
            sortDirection="ascending",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_movie(r) for r in records]

    async def search(self, item_id: int) -> None:
        """Trigger an automatic movie search in Radarr.

        Calls ``POST /api/v3/command`` with command ``MoviesSearch``.

        Args:
            item_id: Radarr movie ID to search for.
        """
        await self._post(
            "/api/v3/command",
            json={"name": "MoviesSearch", "movieIds": [item_id]},
        )

    async def get_cutoff_unmet(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingMovie]:
        """Return a page of monitored movies that have not met their quality cutoff.

        Calls ``GET /api/v3/wanted/cutoff`` sorted by in-cinema date.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingMovie` dataclasses for cutoff-unmet movies.
        """
        data: dict[str, Any] = await self._get(
            "/api/v3/wanted/cutoff",
            page=page,
            pageSize=page_size,
            sortKey="inCinemas",
            sortDirection="ascending",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_movie(r) for r in records]

    async def search_movie(self, movie_id: int) -> None:
        """Alias for :meth:`search` with a more descriptive name."""
        await self.search(movie_id)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_movie(record: dict[str, Any]) -> MissingMovie:
    return MissingMovie(
        movie_id=record["id"],
        title=record.get("title") or "",
        year=record.get("year", 0),
        digital_release=record.get("digitalRelease"),
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_radarr_client(
    url: str,
    api_key: str,
    timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
) -> RadarrClient:
    """Return a :class:`RadarrClient` ready for use as an async context manager."""
    return RadarrClient(url=url, api_key=api_key, timeout=timeout)

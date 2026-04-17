"""Whisparr v3 API client: missing movies/scenes and automatic search.

Whisparr v3 is a Radarr-based application focused on scenes and movies.
Its API mirrors Radarr's ``/api/v3/movie`` structure, but unlike Radarr it
does not expose ``/api/v3/wanted/missing`` or ``/api/v3/wanted/cutoff``
endpoints.  Missing and cutoff-unmet items are identified by fetching the
full library via ``GET /api/v3/movie`` and filtering client-side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from houndarr.clients.base import ArrClient

__all__ = ["LibraryWhisparrV3Movie", "MissingWhisparrV3Movie", "WhisparrV3Client"]


@dataclass(frozen=True)
class MissingWhisparrV3Movie:
    """A missing or cutoff-unmet movie/scene from the Whisparr v3 library."""

    movie_id: int
    title: str
    year: int
    status: str | None
    minimum_availability: str | None
    is_available: bool | None
    in_cinemas: str | None
    physical_release: str | None
    release_date: str | None
    digital_release: str | None


@dataclass(frozen=True)
class LibraryWhisparrV3Movie:
    """A movie/scene from Whisparr v3's full library with file and cutoff metadata."""

    movie_id: int
    title: str
    year: int
    monitored: bool
    has_file: bool
    cutoff_met: bool
    in_cinemas: str | None
    physical_release: str | None
    digital_release: str | None


class WhisparrV3Client(ArrClient):
    """Async client for the Whisparr v3 REST API.

    Whisparr v3 has no ``wanted/missing`` or ``wanted/cutoff`` endpoints.
    This client fetches the full library via ``GET /api/v3/movie`` and
    filters client-side for missing and cutoff-unmet items.  The library
    response is cached for the lifetime of the client instance (one search
    pass) to avoid redundant fetches as pagination advances.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
    ) -> None:
        super().__init__(url=url, api_key=api_key, timeout=timeout)
        self._movie_cache: list[dict[str, Any]] | None = None

    async def _get_all_movies(self) -> list[dict[str, Any]]:
        """Fetch and cache the full movie library from ``GET /api/v3/movie``."""
        if self._movie_cache is None:
            result: list[dict[str, Any]] = await self._get("/api/v3/movie")
            self._movie_cache = result
        return self._movie_cache

    async def get_missing(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingWhisparrV3Movie]:
        """Return a page of monitored missing movies/scenes.

        Since Whisparr v3 has no ``wanted/missing`` endpoint, this fetches
        the full library and filters for ``monitored=True, hasFile=False``.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingWhisparrV3Movie` dataclasses.
        """
        movies = await self._get_all_movies()
        missing = sorted(
            [
                _parse_movie(r)
                for r in movies
                if r.get("monitored", False) and not r.get("hasFile", False)
            ],
            key=lambda m: m.in_cinemas or "",
        )
        start = (page - 1) * page_size
        return missing[start : start + page_size]

    async def search(self, item_id: int) -> None:
        """Trigger an automatic movie/scene search in Whisparr v3.

        Calls ``POST /api/v3/command`` with command ``MoviesSearch``.

        Args:
            item_id: Whisparr v3 movie ID to search for.
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
    ) -> list[MissingWhisparrV3Movie]:
        """Return a page of monitored movies/scenes that have not met their quality cutoff.

        Fetches the full library and filters for items with a file that
        has ``qualityCutoffNotMet=True``.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingWhisparrV3Movie` dataclasses.
        """
        movies = await self._get_all_movies()
        cutoff: list[MissingWhisparrV3Movie] = []
        for r in movies:
            if not r.get("monitored", False) or not r.get("hasFile", False):
                continue
            movie_file: dict[str, Any] = r.get("movieFile") or {}
            if movie_file.get("qualityCutoffNotMet", True):
                cutoff.append(_parse_movie(r))
        cutoff.sort(key=lambda m: m.in_cinemas or "")
        start = (page - 1) * page_size
        return cutoff[start : start + page_size]

    async def get_wanted_total(self, kind: Literal["missing", "cutoff"]) -> int:
        """Return the count of wanted items for *kind* from the cached library.

        Reuses :meth:`_get_all_movies` (one fetch per client lifetime) so the
        probe does not trigger an extra network call during a pass.
        """
        movies = await self._get_all_movies()
        if kind == "missing":
            return sum(
                1 for r in movies if r.get("monitored", False) and not r.get("hasFile", False)
            )
        count = 0
        for r in movies:
            if not r.get("monitored", False) or not r.get("hasFile", False):
                continue
            movie_file: dict[str, Any] = r.get("movieFile") or {}
            if movie_file.get("qualityCutoffNotMet", True):
                count += 1
        return count

    async def get_library(self) -> list[LibraryWhisparrV3Movie]:
        """Return the full movie/scene library.

        Calls ``GET /api/v3/movie``.  Used by the upgrade pass to find
        items that already have files and meet cutoff.

        Returns:
            List of :class:`LibraryWhisparrV3Movie` dataclasses.
        """
        movies = await self._get_all_movies()
        return [_parse_library_movie(r) for r in movies]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_library_movie(record: dict[str, Any]) -> LibraryWhisparrV3Movie:
    has_file = bool(record.get("hasFile", False))
    movie_file: dict[str, Any] = record.get("movieFile") or {}
    cutoff_not_met = movie_file.get("qualityCutoffNotMet", True)
    return LibraryWhisparrV3Movie(
        movie_id=record["id"],
        title=record.get("title") or "",
        year=record.get("year", 0),
        monitored=bool(record.get("monitored", False)),
        has_file=has_file,
        cutoff_met=not cutoff_not_met if has_file else False,
        in_cinemas=record.get("inCinemas"),
        physical_release=record.get("physicalRelease"),
        digital_release=record.get("digitalRelease"),
    )


def _parse_movie(record: dict[str, Any]) -> MissingWhisparrV3Movie:
    return MissingWhisparrV3Movie(
        movie_id=record["id"],
        title=record.get("title") or "",
        year=record.get("year", 0),
        status=record.get("status"),
        minimum_availability=record.get("minimumAvailability"),
        is_available=record.get("isAvailable"),
        in_cinemas=record.get("inCinemas"),
        physical_release=record.get("physicalRelease"),
        release_date=record.get("releaseDate"),
        digital_release=record.get("digitalRelease"),
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_whisparr_v3_client(
    url: str,
    api_key: str,
    timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
) -> WhisparrV3Client:
    """Return a :class:`WhisparrV3Client` ready for use as an async context manager."""
    return WhisparrV3Client(url=url, api_key=api_key, timeout=timeout)

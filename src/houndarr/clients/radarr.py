"""Radarr v3 API client: missing movies and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from houndarr.clients._wire_models import (
    PaginatedResponse,
    RadarrLibraryMovie,
    RadarrWantedMovie,
)
from houndarr.clients.base import ArrClient, WantedKind

__all__ = ["LibraryMovie", "MissingMovie", "RadarrClient"]


@dataclass(frozen=True, slots=True)
class LibraryMovie:
    """A movie from Radarr's full library endpoint."""

    movie_id: int
    title: str
    year: int
    monitored: bool
    has_file: bool
    cutoff_met: bool
    in_cinemas: str | None
    physical_release: str | None
    digital_release: str | None


@dataclass(frozen=True, slots=True)
class MissingMovie:
    """A single missing movie returned by Radarr's wanted/missing endpoint."""

    movie_id: int
    title: str
    year: int
    status: str | None
    minimum_availability: str | None
    is_available: bool | None
    in_cinemas: str | None
    physical_release: str | None
    release_date: str | None
    digital_release: str | None  # ISO-8601 date or None if unknown


class RadarrClient(ArrClient):
    """Async client for the Radarr v3 REST API."""

    # Radarr is the only paginated client whose cutoff endpoint includes
    # the sort params (Sonarr, Lidarr, Readarr, and Whisparr v2 omit them
    # for cutoff); the template's ``include_sort=True`` default captures
    # both passes here.
    _WANTED_SORT_KEY: ClassVar[str] = "inCinemas"
    _WANTED_ENVELOPE: ClassVar[type[PaginatedResponse[RadarrWantedMovie]]] = PaginatedResponse[
        RadarrWantedMovie
    ]

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
        envelope = await self._fetch_wanted_page("missing", page=page, page_size=page_size)
        return [_parse_movie(w) for w in envelope.records]

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
        envelope = await self._fetch_wanted_page("cutoff", page=page, page_size=page_size)
        return [_parse_movie(w) for w in envelope.records]

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

    async def get_library(self) -> list[LibraryMovie]:
        """Return the full movie library.

        Calls ``GET /api/v3/movie`` and returns all movies with metadata
        needed for upgrade-pass eligibility filtering.

        Returns:
            List of :class:`LibraryMovie` dataclasses.
        """
        records = await self._get("/api/v3/movie")
        return [_parse_library_movie(RadarrLibraryMovie.model_validate(r)) for r in records]


# Parsing helpers


def _parse_library_movie(wire: RadarrLibraryMovie) -> LibraryMovie:
    has_file = bool(wire.has_file)
    cutoff_not_met = True
    if wire.movie_file is not None and wire.movie_file.quality_cutoff_not_met is not None:
        cutoff_not_met = wire.movie_file.quality_cutoff_not_met
    return LibraryMovie(
        movie_id=wire.id,
        title=wire.title or "",
        year=wire.year or 0,
        monitored=bool(wire.monitored),
        has_file=has_file,
        cutoff_met=not cutoff_not_met if has_file else False,
        in_cinemas=wire.in_cinemas,
        physical_release=wire.physical_release,
        digital_release=wire.digital_release,
    )


def _parse_movie(wire: RadarrWantedMovie) -> MissingMovie:
    return MissingMovie(
        movie_id=wire.id,
        title=wire.title or "",
        year=wire.year or 0,
        status=wire.status,
        minimum_availability=wire.minimum_availability,
        is_available=wire.is_available,
        in_cinemas=wire.in_cinemas,
        physical_release=wire.physical_release,
        release_date=wire.release_date,
        digital_release=wire.digital_release,
    )

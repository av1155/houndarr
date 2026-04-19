"""Whisparr v3 API client: missing movies/scenes and automatic search.

Whisparr v3 is a Radarr-based application focused on scenes and movies.
Its API mirrors Radarr's ``/api/v3/movie`` structure, but unlike Radarr it
does not expose ``/api/v3/wanted/missing`` or ``/api/v3/wanted/cutoff``
endpoints.  Missing and cutoff-unmet items are identified by fetching the
full library via ``GET /api/v3/movie`` and filtering client-side.

Outlier status (Track C.6).  Sonarr, Radarr, Lidarr, Readarr, and
Whisparr v2 all collapsed onto the
:meth:`~houndarr.clients.base.ArrClient._fetch_wanted_page` /
:meth:`~houndarr.clients.base.ArrClient._fetch_wanted_total` template
in C.1 - C.5; this module deliberately does not.  No paginated
``/wanted`` endpoint exists upstream, so the four ``_WANTED_*`` hooks
on the base ABC stay at their defaults (``_WANTED_ENVELOPE`` is
``None``); calling :meth:`~ArrClient._fetch_wanted_page` here would
raise :class:`NotImplementedError` by design.

Instead, this client fetches the entire library once per client
lifetime and filters in memory: missing = ``monitored and not
hasFile``; cutoff = ``monitored and hasFile and qualityCutoffNotMet``.
Pagination at the API surface (``page`` / ``page_size`` on
:meth:`get_missing` / :meth:`get_cutoff_unmet`) slices the filtered
list rather than triggering further network calls.

The cache lives on ``self._movie_cache`` and survives for the
context manager's lifetime (one search pass).  This keeps the
missing pass, the cutoff pass, and the wanted-total probe from
each issuing their own ``/api/v3/movie`` request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from houndarr.clients._wire_models import WhisparrV3LibraryMovie
from houndarr.clients.base import ArrClient, InstanceSnapshot, WantedKind

__all__ = ["LibraryWhisparrV3Movie", "MissingWhisparrV3Movie", "WhisparrV3Client"]


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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

    Whisparr v3 has no ``wanted/missing`` or ``wanted/cutoff`` endpoints,
    so it does not use the shared ``/wanted`` template the other five
    clients adopted in C.1 - C.5.  All four ``_WANTED_*`` class-level
    hooks stay at the base defaults; ``_WANTED_ENVELOPE`` remains
    ``None`` so an accidental call to
    :meth:`~houndarr.clients.base.ArrClient._fetch_wanted_page` raises
    :class:`NotImplementedError` rather than silently producing an
    empty page.

    Missing and cutoff-unmet items are computed from a one-shot
    ``GET /api/v3/movie`` fetch, cached for the lifetime of the client
    instance.  :meth:`get_wanted_total` reuses the same cache so a
    single network call answers every per-pass query.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
    ) -> None:
        super().__init__(url=url, api_key=api_key, timeout=timeout)
        self._movie_cache: list[WhisparrV3LibraryMovie] | None = None

    async def _get_all_movies(self) -> list[WhisparrV3LibraryMovie]:
        """Fetch and cache the full movie library from ``GET /api/v3/movie``."""
        if self._movie_cache is None:
            result = await self._get("/api/v3/movie")
            self._movie_cache = [WhisparrV3LibraryMovie.model_validate(r) for r in result]
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
            [_parse_movie(m) for m in movies if m.monitored and not m.has_file],
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
        for m in movies:
            if not m.monitored or not m.has_file:
                continue
            cutoff_not_met = True
            if m.movie_file is not None and m.movie_file.quality_cutoff_not_met is not None:
                cutoff_not_met = m.movie_file.quality_cutoff_not_met
            if cutoff_not_met:
                cutoff.append(_parse_movie(m))
        cutoff.sort(key=lambda m: m.in_cinemas or "")
        start = (page - 1) * page_size
        return cutoff[start : start + page_size]

    async def get_wanted_total(self, kind: WantedKind) -> int:
        """Return the count of wanted items for *kind* from the cached library.

        Overrides the base default
        (:meth:`~houndarr.clients.base.ArrClient._fetch_wanted_total`)
        because there is no ``/wanted`` endpoint to probe here; the
        total is derived from the cached ``/api/v3/movie`` payload.

        Reuses :meth:`_get_all_movies` (one fetch per client lifetime) so the
        probe does not trigger an extra network call during a pass.
        """
        movies = await self._get_all_movies()
        if kind == "missing":
            return sum(1 for m in movies if m.monitored and not m.has_file)
        count = 0
        for m in movies:
            if not m.monitored or not m.has_file:
                continue
            cutoff_not_met = True
            if m.movie_file is not None and m.movie_file.quality_cutoff_not_met is not None:
                cutoff_not_met = m.movie_file.quality_cutoff_not_met
            if cutoff_not_met:
                count += 1
        return count

    async def get_instance_snapshot(self) -> InstanceSnapshot:
        """Compute monitored + unreleased counts from the cached library.

        Unlike the paginated /wanted endpoints the other arrs expose,
        Whisparr v3 only publishes full-library ``/api/v3/movie``.  A
        single fetch (cached across the pass) answers both the monitored
        total and the count of monitored items with a future release
        date.
        """
        movies = await self._get_all_movies()
        now_iso = datetime.now(UTC).isoformat(timespec="seconds")
        monitored_total = 0
        unreleased_count = 0
        for m in movies:
            if not m.monitored:
                continue
            has_file = bool(m.has_file)
            cutoff_unmet = True
            if m.movie_file is not None and m.movie_file.quality_cutoff_not_met is not None:
                cutoff_unmet = m.movie_file.quality_cutoff_not_met
            if (not has_file) or (has_file and cutoff_unmet):
                monitored_total += 1
            for val in (m.digital_release, m.physical_release, m.in_cinemas, m.release_date):
                if isinstance(val, str) and val > now_iso:
                    unreleased_count += 1
                    break
        return InstanceSnapshot(
            monitored_total=monitored_total,
            unreleased_count=unreleased_count,
        )

    async def get_library(self) -> list[LibraryWhisparrV3Movie]:
        """Return the full movie/scene library.

        Calls ``GET /api/v3/movie``.  Used by the upgrade pass to find
        items that already have files and meet cutoff.

        Returns:
            List of :class:`LibraryWhisparrV3Movie` dataclasses.
        """
        movies = await self._get_all_movies()
        return [_parse_library_movie(m) for m in movies]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_library_movie(wire: WhisparrV3LibraryMovie) -> LibraryWhisparrV3Movie:
    has_file = bool(wire.has_file)
    cutoff_not_met = True
    if wire.movie_file is not None and wire.movie_file.quality_cutoff_not_met is not None:
        cutoff_not_met = wire.movie_file.quality_cutoff_not_met
    return LibraryWhisparrV3Movie(
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


def _parse_movie(wire: WhisparrV3LibraryMovie) -> MissingWhisparrV3Movie:
    return MissingWhisparrV3Movie(
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

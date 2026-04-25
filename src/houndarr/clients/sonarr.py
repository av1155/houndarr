"""Sonarr v3 API client: missing episodes and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from houndarr.clients._wire_models import (
    ArrSeries,
    PaginatedResponse,
    SonarrLibraryEpisode,
    SonarrWantedEpisode,
)
from houndarr.clients.base import ArrClient, WantedKind

__all__ = ["LibraryEpisode", "MissingEpisode", "SonarrClient"]


@dataclass(frozen=True, slots=True)
class LibraryEpisode:
    """An episode from Sonarr's full library with file and cutoff metadata."""

    episode_id: int
    series_id: int
    series_title: str
    episode_title: str
    season: int
    episode: int
    monitored: bool
    has_file: bool
    cutoff_met: bool


@dataclass(frozen=True, slots=True)
class MissingEpisode:
    """A single missing episode returned by Sonarr's wanted/missing endpoint."""

    episode_id: int
    series_id: int | None
    series_title: str
    episode_title: str
    season: int
    episode: int
    air_date_utc: str | None  # ISO-8601 or None if not yet aired


class SonarrClient(ArrClient):
    """Async client for the Sonarr v3 REST API."""

    _WANTED_SORT_KEY: ClassVar[str] = "airDateUtc"
    _WANTED_INCLUDE_PARAM: ClassVar[str | None] = "includeSeries"
    _WANTED_ENVELOPE: ClassVar[type[PaginatedResponse[SonarrWantedEpisode]]] = PaginatedResponse[
        SonarrWantedEpisode
    ]

    async def get_missing(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingEpisode]:
        """Return a page of monitored missing episodes.

        Calls ``GET /api/v3/wanted/missing`` with ``includeSeries=true`` so
        that series metadata (title) is embedded in each record.

        Args:
            page: 1-based page number.
            page_size: Number of records per page (max 250 in Sonarr).

        Returns:
            List of :class:`MissingEpisode` dataclasses, oldest first.
        """
        envelope = await self._fetch_wanted_page("missing", page=page, page_size=page_size)
        return [_parse_episode(w) for w in envelope.records]

    async def search(self, item_id: int) -> None:
        """Trigger an automatic episode search in Sonarr.

        Calls ``POST /api/v3/command`` with command ``EpisodeSearch``.

        Args:
            item_id: Sonarr episode ID to search for.
        """
        await self._post(
            "/api/v3/command",
            json={"name": "EpisodeSearch", "episodeIds": [item_id]},
        )

    async def search_season(self, series_id: int, season_number: int) -> None:
        """Trigger a season-context search in Sonarr.

        Calls ``POST /api/v3/command`` with command ``SeasonSearch``.

        Args:
            series_id: Sonarr series ID.
            season_number: Sonarr season number.
        """
        await self._post(
            "/api/v3/command",
            json={"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
        )

    async def get_cutoff_unmet(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingEpisode]:
        """Return a page of monitored episodes that have not met their quality cutoff.

        Calls ``GET /api/v3/wanted/cutoff`` with ``includeSeries=true`` so that
        series metadata is embedded in each record.  Sonarr's cutoff
        endpoint historically omits the sort params, so the call passes
        ``include_sort=False`` to suppress them.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingEpisode` dataclasses for cutoff-unmet episodes.
        """
        envelope = await self._fetch_wanted_page(
            "cutoff",
            page=page,
            page_size=page_size,
            include_sort=False,
        )
        return [_parse_episode(w) for w in envelope.records]

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

    async def get_series(self) -> list[ArrSeries]:
        """Return the full series list.

        Calls ``GET /api/v3/series``.  The upgrade-pass adapter filters on
        ``monitored`` and ``id``; other fields on the response are ignored.

        Returns:
            List of :class:`ArrSeries` wire models.
        """
        result = await self._get("/api/v3/series")
        return [ArrSeries.model_validate(r) for r in result]

    async def get_episodes(self, series_id: int) -> list[LibraryEpisode]:
        """Return all episodes for a series with file and cutoff metadata.

        Calls ``GET /api/v3/episode`` with ``seriesId``,
        ``includeEpisodeFile=true``, and ``includeSeries=true``.

        Args:
            series_id: Sonarr series ID.

        Returns:
            List of :class:`LibraryEpisode` dataclasses.
        """
        records = await self._get(
            "/api/v3/episode",
            seriesId=series_id,
            includeEpisodeFile="true",
            includeSeries="true",
        )
        return [_parse_library_episode(SonarrLibraryEpisode.model_validate(r)) for r in records]


# Parsing helpers


def _parse_library_episode(wire: SonarrLibraryEpisode) -> LibraryEpisode:
    has_file = bool(wire.has_file)
    cutoff_not_met = True
    if wire.episode_file is not None and wire.episode_file.quality_cutoff_not_met is not None:
        cutoff_not_met = wire.episode_file.quality_cutoff_not_met
    series_id = wire.series_id or (wire.series.id if wire.series else None) or 0
    series_title = (wire.series.title if wire.series else None) or ""
    return LibraryEpisode(
        episode_id=wire.id,
        series_id=series_id,
        series_title=series_title,
        episode_title=wire.title or "",
        season=wire.season_number or 0,
        episode=wire.episode_number or 0,
        monitored=bool(wire.monitored),
        has_file=has_file,
        cutoff_met=not cutoff_not_met if has_file else False,
    )


def _parse_episode(wire: SonarrWantedEpisode) -> MissingEpisode:
    series_id = (
        wire.series_id if wire.series_id is not None else (wire.series.id if wire.series else None)
    )
    series_title = (wire.series.title if wire.series else None) or wire.series_title or ""
    return MissingEpisode(
        episode_id=wire.id,
        series_id=series_id,
        series_title=series_title,
        episode_title=wire.title or "",
        season=wire.season_number or 0,
        episode=wire.episode_number or 0,
        air_date_utc=wire.air_date_utc,
    )

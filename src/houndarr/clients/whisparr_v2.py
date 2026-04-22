"""Whisparr v2 API client: missing episodes and automatic search.

Whisparr v2 is a Sonarr fork with the same v3 API structure.  Key differences:
episodes use a date string (ISO ``YYYY-MM-DD``) in the ``releaseDate`` field
instead of Sonarr's ``airDateUtc``, and there is no ``episodeNumber`` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from houndarr.clients._wire_models import (
    ArrSeries,
    PaginatedResponse,
    WhisparrV2LibraryEpisode,
    WhisparrV2WantedEpisode,
)
from houndarr.clients.base import ArrClient

__all__ = ["LibraryWhisparrEpisode", "MissingWhisparrEpisode", "WhisparrClient"]


@dataclass(frozen=True)
class LibraryWhisparrEpisode:
    """An episode from Whisparr's full library with file and cutoff metadata."""

    episode_id: int
    series_id: int
    series_title: str
    episode_title: str
    season_number: int
    absolute_episode_number: int | None
    monitored: bool
    has_file: bool
    cutoff_met: bool


@dataclass(frozen=True)
class MissingWhisparrEpisode:
    """A single missing episode returned by Whisparr's wanted/missing endpoint."""

    episode_id: int
    series_id: int | None
    series_title: str
    episode_title: str
    season_number: int
    absolute_episode_number: int | None
    release_date: datetime | None  # parsed from DateOnly {year, month, day}


class WhisparrClient(ArrClient):
    """Async client for the Whisparr v3 REST API."""

    async def get_missing(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingWhisparrEpisode]:
        """Return a page of monitored missing episodes.

        Calls ``GET /api/v3/wanted/missing`` with ``includeSeries=true``
        so that series metadata is embedded in each record.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingWhisparrEpisode` dataclasses, oldest first.
        """
        data = await self._get(
            "/api/v3/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="releaseDate",
            sortDirection="ascending",
            includeSeries="true",
            monitored="true",
        )
        envelope = PaginatedResponse[WhisparrV2WantedEpisode].model_validate(data)
        return [_parse_episode(w) for w in envelope.records]

    async def search(self, item_id: int) -> None:
        """Trigger an automatic episode search in Whisparr.

        Calls ``POST /api/v3/command`` with command ``EpisodeSearch``.

        Args:
            item_id: Whisparr episode ID to search for.
        """
        await self._post(
            "/api/v3/command",
            json={"name": "EpisodeSearch", "episodeIds": [item_id]},
        )

    async def search_season(self, series_id: int, season_number: int) -> None:
        """Trigger a season-context search in Whisparr.

        Calls ``POST /api/v3/command`` with command ``SeasonSearch``.

        Args:
            series_id: Whisparr series ID.
            season_number: Whisparr season number.
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
    ) -> list[MissingWhisparrEpisode]:
        """Return a page of monitored episodes that have not met their quality cutoff.

        Calls ``GET /api/v3/wanted/cutoff`` with ``includeSeries=true``.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingWhisparrEpisode` dataclasses.
        """
        data = await self._get(
            "/api/v3/wanted/cutoff",
            page=page,
            pageSize=page_size,
            includeSeries="true",
            monitored="true",
        )
        envelope = PaginatedResponse[WhisparrV2WantedEpisode].model_validate(data)
        return [_parse_episode(w) for w in envelope.records]

    async def get_wanted_total(self, kind: Literal["missing", "cutoff"]) -> int:
        """Return the totalRecords count for ``wanted/{kind}`` via a size-1 probe."""
        data = await self._get(
            f"/api/v3/wanted/{kind}",
            page=1,
            pageSize=1,
            sortKey="releaseDate",
            sortDirection="ascending",
            monitored="true",
        )
        envelope = PaginatedResponse[WhisparrV2WantedEpisode].model_validate(data)
        return envelope.total_records

    async def get_series(self) -> list[ArrSeries]:
        """Return the full series list.

        Calls ``GET /api/v3/series``.  The upgrade-pass adapter filters on
        ``monitored`` and ``id``; other fields on the response are ignored.

        Returns:
            List of :class:`ArrSeries` wire models.
        """
        result = await self._get("/api/v3/series")
        return [ArrSeries.model_validate(r) for r in result]

    async def get_episodes(self, series_id: int) -> list[LibraryWhisparrEpisode]:
        """Return all episodes for a series with file and cutoff metadata.

        Calls ``GET /api/v3/episode`` with ``seriesId``,
        ``includeEpisodeFile=true``, and ``includeSeries=true``.

        Args:
            series_id: Whisparr series ID.

        Returns:
            List of :class:`LibraryWhisparrEpisode` dataclasses.
        """
        records = await self._get(
            "/api/v3/episode",
            seriesId=series_id,
            includeEpisodeFile="true",
            includeSeries="true",
        )
        return [_parse_library_episode(WhisparrV2LibraryEpisode.model_validate(r)) for r in records]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_library_episode(wire: WhisparrV2LibraryEpisode) -> LibraryWhisparrEpisode:
    has_file = bool(wire.has_file)
    cutoff_not_met = True
    if wire.episode_file is not None and wire.episode_file.quality_cutoff_not_met is not None:
        cutoff_not_met = wire.episode_file.quality_cutoff_not_met
    series_id = wire.series_id or (wire.series.id if wire.series else None) or 0
    series_title = (wire.series.title if wire.series else None) or ""
    return LibraryWhisparrEpisode(
        episode_id=wire.id,
        series_id=series_id,
        series_title=series_title,
        episode_title=wire.title or "",
        season_number=wire.season_number or 0,
        absolute_episode_number=wire.absolute_episode_number,
        monitored=bool(wire.monitored),
        has_file=has_file,
        cutoff_met=not cutoff_not_met if has_file else False,
    )


def _parse_date_only(obj: dict[str, int] | str | None) -> datetime | None:
    """Convert a Whisparr release date to a UTC datetime.

    The v2 API serialises .NET's ``System.DateOnly`` as a plain ISO date
    string (``"2026-04-03"``), not as the ``{year, month, day}`` object
    that the OpenAPI spec generator describes.  This function handles both
    formats for safety.

    Returns ``None`` if the input is missing, empty, or has invalid values.
    """
    if not obj:
        return None
    if isinstance(obj, str):
        try:
            return datetime.fromisoformat(obj).replace(tzinfo=UTC)
        except ValueError:
            return None
    try:
        return datetime(obj["year"], obj["month"], obj["day"], tzinfo=UTC)
    except (KeyError, TypeError, ValueError):
        return None


def _parse_episode(wire: WhisparrV2WantedEpisode) -> MissingWhisparrEpisode:
    series_id = (
        wire.series_id if wire.series_id is not None else (wire.series.id if wire.series else None)
    )
    series_title = (wire.series.title if wire.series else None) or wire.series_title or ""
    return MissingWhisparrEpisode(
        episode_id=wire.id,
        series_id=series_id,
        series_title=series_title,
        episode_title=wire.title or "",
        season_number=wire.season_number or 0,
        absolute_episode_number=wire.absolute_episode_number,
        release_date=_parse_date_only(wire.release_date),
    )

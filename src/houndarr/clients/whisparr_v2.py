"""Whisparr v2 API client: missing episodes and automatic search.

Whisparr v2 is a Sonarr fork with the same v3 API structure.  Key differences:
episodes use a date string (ISO ``YYYY-MM-DD``) in the ``releaseDate`` field
instead of Sonarr's ``airDateUtc``, and there is no ``episodeNumber`` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

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
        data: dict[str, Any] = await self._get(
            "/api/v3/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="releaseDate",
            sortDirection="ascending",
            includeSeries="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_episode(r) for r in records]

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
        data: dict[str, Any] = await self._get(
            "/api/v3/wanted/cutoff",
            page=page,
            pageSize=page_size,
            includeSeries="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_episode(r) for r in records]

    async def get_series(self) -> list[dict[str, Any]]:
        """Return the full series list.

        Calls ``GET /api/v3/series``.  Returns raw dicts; only ``id`` and
        ``monitored`` are needed by the upgrade-pass adapter.

        Returns:
            List of series dicts from Whisparr.
        """
        result: list[dict[str, Any]] = await self._get("/api/v3/series")
        return result

    async def get_episodes(self, series_id: int) -> list[LibraryWhisparrEpisode]:
        """Return all episodes for a series with file and cutoff metadata.

        Calls ``GET /api/v3/episode`` with ``seriesId``,
        ``includeEpisodeFile=true``, and ``includeSeries=true``.

        Args:
            series_id: Whisparr series ID.

        Returns:
            List of :class:`LibraryWhisparrEpisode` dataclasses.
        """
        records: list[dict[str, Any]] = await self._get(
            "/api/v3/episode",
            seriesId=series_id,
            includeEpisodeFile="true",
            includeSeries="true",
        )
        return [_parse_library_episode(r) for r in records]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_library_episode(record: dict[str, Any]) -> LibraryWhisparrEpisode:
    series: dict[str, Any] = record.get("series") or {}
    has_file = bool(record.get("hasFile", False))
    ep_file: dict[str, Any] = record.get("episodeFile") or {}
    cutoff_not_met = ep_file.get("qualityCutoffNotMet", True)
    return LibraryWhisparrEpisode(
        episode_id=record["id"],
        series_id=record.get("seriesId") or series.get("id") or 0,
        series_title=series.get("title") or "",
        episode_title=record.get("title") or "",
        season_number=record.get("seasonNumber", 0),
        absolute_episode_number=record.get("absoluteEpisodeNumber"),
        monitored=bool(record.get("monitored", False)),
        has_file=has_file,
        cutoff_met=not cutoff_not_met if has_file else False,
    )


def _parse_date_only(obj: dict[str, Any] | str | None) -> datetime | None:
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


def _parse_episode(record: dict[str, Any]) -> MissingWhisparrEpisode:
    series: dict[str, Any] = record.get("series") or {}
    return MissingWhisparrEpisode(
        episode_id=record["id"],
        series_id=record.get("seriesId") or series.get("id"),
        series_title=series.get("title") or record.get("seriesTitle") or "",
        episode_title=record.get("title") or "",
        season_number=record.get("seasonNumber", 0),
        absolute_episode_number=record.get("absoluteEpisodeNumber"),
        release_date=_parse_date_only(record.get("releaseDate")),
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_whisparr_client(
    url: str,
    api_key: str,
    timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
) -> WhisparrClient:
    """Return a :class:`WhisparrClient` ready for use as an async context manager."""
    return WhisparrClient(url=url, api_key=api_key, timeout=timeout)

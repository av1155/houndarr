"""Sonarr v3 API client: missing episodes and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from houndarr.clients.base import ArrClient

__all__ = ["LibraryEpisode", "MissingEpisode", "SonarrClient"]


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
        data: dict[str, Any] = await self._get(
            "/api/v3/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="airDateUtc",
            sortDirection="ascending",
            includeSeries="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_episode(r) for r in records]

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
        series metadata is embedded in each record.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingEpisode` dataclasses for cutoff-unmet episodes.
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

    async def get_wanted_total(self, kind: Literal["missing", "cutoff"]) -> int:
        """Return the totalRecords count for ``wanted/{kind}`` via a size-1 probe."""
        data: dict[str, Any] = await self._get(
            f"/api/v3/wanted/{kind}",
            page=1,
            pageSize=1,
            sortKey="airDateUtc",
            sortDirection="ascending",
            monitored="true",
        )
        return int(data.get("totalRecords", 0) or 0)

    async def get_series(self) -> list[dict[str, Any]]:
        """Return the full series list.

        Calls ``GET /api/v3/series``.  Returns raw dicts; only ``id`` and
        ``monitored`` are needed by the upgrade-pass adapter.

        Returns:
            List of series dicts from Sonarr.
        """
        result: list[dict[str, Any]] = await self._get("/api/v3/series")
        return result

    async def get_episodes(self, series_id: int) -> list[LibraryEpisode]:
        """Return all episodes for a series with file and cutoff metadata.

        Calls ``GET /api/v3/episode`` with ``seriesId``,
        ``includeEpisodeFile=true``, and ``includeSeries=true``.

        Args:
            series_id: Sonarr series ID.

        Returns:
            List of :class:`LibraryEpisode` dataclasses.
        """
        records: list[dict[str, Any]] = await self._get(
            "/api/v3/episode",
            seriesId=series_id,
            includeEpisodeFile="true",
            includeSeries="true",
        )
        return [_parse_library_episode(r) for r in records]

    async def search_episode(self, episode_id: int) -> None:
        """Alias for :meth:`search` with a more descriptive name."""
        await self.search(episode_id)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_library_episode(record: dict[str, Any]) -> LibraryEpisode:
    series: dict[str, Any] = record.get("series") or {}
    has_file = bool(record.get("hasFile", False))
    ep_file: dict[str, Any] = record.get("episodeFile") or {}
    cutoff_not_met = ep_file.get("qualityCutoffNotMet", True)
    return LibraryEpisode(
        episode_id=record["id"],
        series_id=record.get("seriesId") or series.get("id") or 0,
        series_title=series.get("title") or "",
        episode_title=record.get("title") or "",
        season=record.get("seasonNumber", 0),
        episode=record.get("episodeNumber", 0),
        monitored=bool(record.get("monitored", False)),
        has_file=has_file,
        cutoff_met=not cutoff_not_met if has_file else False,
    )


def _parse_episode(record: dict[str, Any]) -> MissingEpisode:
    series: dict[str, Any] = record.get("series") or {}
    return MissingEpisode(
        episode_id=record["id"],
        series_id=record.get("seriesId") or series.get("id"),
        series_title=series.get("title") or record.get("seriesTitle") or "",
        episode_title=record.get("title") or "",
        season=record.get("seasonNumber", 0),
        episode=record.get("episodeNumber", 0),
        air_date_utc=record.get("airDateUtc"),
    )


# ---------------------------------------------------------------------------
# Convenience factory (mirrors httpx.AsyncClient signature)
# ---------------------------------------------------------------------------


def make_sonarr_client(
    url: str,
    api_key: str,
    timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
) -> SonarrClient:
    """Return a :class:`SonarrClient` ready for use as an async context manager."""
    return SonarrClient(url=url, api_key=api_key, timeout=timeout)

"""Sonarr v3 API client — missing episodes and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from houndarr.clients.base import ArrClient

__all__ = ["MissingEpisode", "SonarrClient"]


@dataclass(frozen=True)
class MissingEpisode:
    """A single missing episode returned by Sonarr's wanted/missing endpoint."""

    episode_id: int
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

    async def search_episode(self, episode_id: int) -> None:
        """Alias for :meth:`search` with a more descriptive name."""
        await self.search(episode_id)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_episode(record: dict[str, Any]) -> MissingEpisode:
    series: dict[str, Any] = record.get("series") or {}
    return MissingEpisode(
        episode_id=record["id"],
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

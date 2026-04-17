"""Minimal *arr API mock for Houndarr's browser end-to-end tests.

Serves the endpoints Houndarr actually calls: system status, queue
status, wanted/missing, wanted/cutoff, command dispatch, and the
library endpoints used by the upgrade pass.  One process per *arr flavor
selected with ``--app Sonarr|Radarr``.

Not a general-purpose mock; payload shapes only carry the fields
Houndarr's clients parse.  Values are deterministic so the e2e suite
can assert exact counts.
"""

from __future__ import annotations

import argparse
from typing import Any

import uvicorn
from fastapi import FastAPI

_SONARR_MISSING_TOTAL = 10
_RADARR_MISSING_TOTAL = 10


def _radarr_movie(movie_id: int, *, has_file: bool = False) -> dict[str, Any]:
    return {
        "id": movie_id,
        "title": f"Mock Movie {movie_id}",
        "year": 2020 + (movie_id % 5),
        "status": "released",
        "minimumAvailability": "released",
        "isAvailable": True,
        "inCinemas": "2020-01-01T00:00:00Z",
        "physicalRelease": "2020-06-01T00:00:00Z",
        "digitalRelease": "2020-06-15T00:00:00Z",
        "releaseDate": "2020-06-15T00:00:00Z",
        "monitored": True,
        "hasFile": has_file,
        "movieFile": {"qualityCutoffNotMet": False} if has_file else None,
    }


def _sonarr_episode(episode_id: int) -> dict[str, Any]:
    return {
        "id": episode_id,
        "seriesId": 1,
        "title": f"Mock Episode {episode_id}",
        "seasonNumber": 1,
        "episodeNumber": episode_id,
        "airDateUtc": "2020-01-01T00:00:00Z",
        "monitored": True,
        "series": {"id": 1, "title": "Mock Series", "monitored": True},
    }


def make_app(app_name: str, version: str = "4.0.0") -> FastAPI:
    """Build a FastAPI app that answers the *arr endpoints Houndarr calls."""
    app = FastAPI(title=f"Mock {app_name}", version=version)

    @app.get("/api/v3/system/status")
    async def status() -> dict[str, Any]:
        return {
            "appName": app_name,
            "version": version,
            "authentication": "none",
            "instanceName": app_name,
        }

    @app.get("/api/v3/queue/status")
    async def queue_status() -> dict[str, Any]:
        return {
            "totalCount": 0,
            "count": 0,
            "unknownCount": 0,
            "errors": False,
            "warnings": False,
            "unknownErrors": False,
            "unknownWarnings": False,
        }

    @app.get("/api/v3/wanted/missing")
    async def wanted_missing(page: int = 1, pageSize: int = 10) -> dict[str, Any]:  # noqa: N803
        if app_name == "Radarr":
            records = [_radarr_movie(i) for i in range(1, _RADARR_MISSING_TOTAL + 1)]
        else:
            records = [_sonarr_episode(i) for i in range(1, _SONARR_MISSING_TOTAL + 1)]
        start = (page - 1) * pageSize
        return {
            "page": page,
            "pageSize": pageSize,
            "totalRecords": len(records),
            "records": records[start : start + pageSize],
        }

    @app.get("/api/v3/wanted/cutoff")
    async def wanted_cutoff(page: int = 1, pageSize: int = 10) -> dict[str, Any]:  # noqa: N803
        return {"page": page, "pageSize": pageSize, "totalRecords": 0, "records": []}

    @app.post("/api/v3/command")
    async def command(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"id": 1, "name": "MockSearch", "status": "queued"}

    @app.get("/api/v3/movie")
    async def movie_library() -> list[dict[str, Any]]:
        return [_radarr_movie(i, has_file=True) for i in range(100, 105)]

    @app.get("/api/v3/series")
    async def series_library() -> list[dict[str, Any]]:
        return [{"id": 1, "title": "Mock Series", "monitored": True}]

    @app.get("/api/v3/episode")
    async def episodes(seriesId: int | None = None) -> list[dict[str, Any]]:  # noqa: N803
        return []

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock *arr service for Houndarr e2e")
    parser.add_argument("--app", required=True, choices=["Sonarr", "Radarr"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104
    args = parser.parse_args()

    app = make_app(args.app)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

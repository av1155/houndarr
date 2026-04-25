"""Radarr mock router.

Radarr has no parent aggregate: every movie is a top-level record. The
seeder produces a flat list of monitored movies; the partition decides
which are missing (no file), cutoff-unmet, or upgrade-eligible.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from tests.mock_arr._common import (
    attach_common_routes,
    paginate,
    partition_leaf_ids,
)
from tests.mock_arr.store import AppData


def make_radarr_data(
    *,
    seed: int = 42,
    movie_count: int = 500,
    missing_ratio: float = 0.5,
    cutoff_ratio: float = 0.2,
) -> AppData:
    """Build the seeded Radarr-shaped record set."""
    rng = random.Random(seed)
    base_date = datetime(2010, 1, 1, tzinfo=UTC)
    leaves: list[dict[str, Any]] = []
    for movie_id in range(1, movie_count + 1):
        title = f"Mock Radarr Movie {movie_id:03d}"
        in_cinemas = base_date + timedelta(days=movie_id * 3)
        physical = in_cinemas + timedelta(days=120)
        digital = in_cinemas + timedelta(days=60)
        leaves.append(
            {
                "id": movie_id,
                "title": title,
                "originalTitle": title,
                "sortTitle": title.lower(),
                "year": 2010 + (movie_id // 100),
                "tmdbId": 3_000_000 + movie_id,
                "imdbId": f"tt{7_000_000 + movie_id}",
                "titleSlug": f"mock-radarr-movie-{movie_id:03d}",
                "status": "released",
                "minimumAvailability": "released",
                "isAvailable": True,
                "inCinemas": in_cinemas.isoformat().replace("+00:00", "Z"),
                "physicalRelease": physical.isoformat().replace("+00:00", "Z"),
                "digitalRelease": digital.isoformat().replace("+00:00", "Z"),
                "releaseDate": digital.isoformat().replace("+00:00", "Z"),
            }
        )

    rng.shuffle(leaves)
    leaves.sort(key=lambda x: x["id"])

    leaf_ids = [leaf["id"] for leaf in leaves]
    missing_ids, cutoff_ids, upgrade_ids = partition_leaf_ids(
        leaf_ids,
        seed=seed,
        missing_ratio=missing_ratio,
        cutoff_ratio=cutoff_ratio,
    )

    return AppData(
        app_name="Radarr",
        app_version="6.1.1.10360",
        api_prefix="/radarr/api/v3",
        api_version="v3",
        sort_key_default="movieMetadata.sortTitle",
        sort_direction_default="ascending",
        parents=[],
        leaves=leaves,
        missing_ids=missing_ids,
        cutoff_ids=cutoff_ids,
        upgrade_ids=upgrade_ids,
    )


def _library_shape(record: dict[str, Any], data: AppData) -> dict[str, Any]:
    """Return a movie in the ``/movie`` library shape with file metadata."""
    movie_id = record["id"]
    has_file = movie_id not in data.missing_ids
    cutoff_not_met = movie_id in data.cutoff_ids
    payload = {
        **record,
        "monitored": True,
        "hasFile": has_file,
    }
    payload["movieFile"] = (
        {"id": movie_id, "qualityCutoffNotMet": cutoff_not_met} if has_file else None
    )
    return payload


def make_radarr_router(data: AppData) -> APIRouter:
    router = APIRouter(prefix=data.api_prefix)
    attach_common_routes(router, data)

    @router.get("/wanted/missing")
    async def wanted_missing(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=2000, alias="pageSize"),
        sort_key: str = Query("inCinemas", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
    ) -> dict[str, Any]:
        data.page_log.entries.append(("missing", page, page_size))
        records = [leaf for leaf in data.leaves if leaf["id"] in data.missing_ids]
        return paginate(
            records,
            page=page,
            page_size=page_size,
            sort_key=sort_key,
            sort_direction=sort_direction,
        )

    @router.get("/wanted/cutoff")
    async def wanted_cutoff(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=2000, alias="pageSize"),
        sort_key: str = Query("inCinemas", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
    ) -> dict[str, Any]:
        data.page_log.entries.append(("cutoff", page, page_size))
        records = [leaf for leaf in data.leaves if leaf["id"] in data.cutoff_ids]
        return paginate(
            records,
            page=page,
            page_size=page_size,
            sort_key=sort_key,
            sort_direction=sort_direction,
        )

    @router.get("/movie")
    async def get_movies() -> list[dict[str, Any]]:
        return [_library_shape(leaf, data) for leaf in data.leaves]

    return router

"""Whisparr v3 mock router.

Whisparr v3 has no ``/wanted`` endpoints. Houndarr's client fetches the
full ``/api/v3/movie`` library once per probe and computes the missing
and cutoff partitions in memory. The mock therefore only needs to expose
``/movie`` plus the common status / queue / command routes.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter

from tests.mock_arr._common import attach_common_routes, partition_leaf_ids
from tests.mock_arr.store import AppData


def make_whisparr_v3_data(
    *,
    seed: int = 42,
    movie_count: int = 500,
    missing_ratio: float = 0.5,
    cutoff_ratio: float = 0.2,
) -> AppData:
    rng = random.Random(seed)
    leaves: list[dict[str, Any]] = []
    base_date = datetime(2020, 1, 1, tzinfo=UTC)
    for movie_id in range(1, movie_count + 1):
        title = f"Mock WhisparrV3 Scene {movie_id:03d}"
        release_date = base_date + timedelta(days=movie_id)
        leaves.append(
            {
                "id": movie_id,
                "title": title,
                "code": f"SCN-{movie_id:05d}",
                "sortTitle": title.lower(),
                "year": 2020 + (movie_id // 200),
                "studioTitle": f"Mock Studio {1 + (movie_id % 25):02d}",
                "studioForeignId": f"st-{1 + (movie_id % 25):04d}",
                "foreignId": f"sc-{movie_id:08d}",
                "stashId": f"stash-{movie_id:08d}",
                "performerNames": [f"Performer {(movie_id % 30) + 1:02d}"],
                "performerForeignIds": [f"pf-{(movie_id % 30) + 1:04d}"],
                "status": "released",
                "minimumAvailability": "released",
                "isAvailable": True,
                "itemType": "scene",
                "releaseDate": release_date.isoformat().replace("+00:00", "Z"),
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
        app_name="Whisparr",
        app_version="3.3.3.683",
        api_prefix="/whisparr_v3/api/v3",
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
    leaf_id = record["id"]
    has_file = leaf_id not in data.missing_ids
    cutoff_not_met = leaf_id in data.cutoff_ids
    payload = {
        **record,
        "monitored": True,
        "hasFile": has_file,
    }
    payload["movieFile"] = (
        {"id": leaf_id, "qualityCutoffNotMet": cutoff_not_met} if has_file else None
    )
    return payload


def make_whisparr_v3_router(data: AppData) -> APIRouter:
    router = APIRouter(prefix=data.api_prefix)
    attach_common_routes(router, data)

    @router.get("/movie")
    async def get_movies() -> list[dict[str, Any]]:
        return [_library_shape(leaf, data) for leaf in data.leaves]

    return router

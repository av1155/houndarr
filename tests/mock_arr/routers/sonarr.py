"""Sonarr mock router.

Exposes ``/api/v3/{system/status, queue/status, command, wanted/missing,
wanted/cutoff, series, episode}`` shaped to match a real Sonarr v4 instance.
The seeder builds a parent/leaf tree (series -> episodes) where every leaf
falls into exactly one of the three engine passes.
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


def make_sonarr_data(
    *,
    seed: int = 42,
    series_count: int = 50,
    episodes_per_series: int = 10,
    missing_ratio: float = 0.5,
    cutoff_ratio: float = 0.2,
) -> AppData:
    """Build the seeded Sonarr-shaped record set.

    With the defaults this yields 50 series * 10 episodes = 500 leaves,
    partitioned 50% missing / 20% cutoff-unmet / 30% upgrade-eligible.
    """
    rng = random.Random(seed)
    parents: list[dict[str, Any]] = []
    leaves: list[dict[str, Any]] = []
    leaf_id = 1000
    base_date = datetime(2020, 1, 1, tzinfo=UTC)
    for series_id in range(1, series_count + 1):
        title = f"Mock Sonarr Series {series_id:03d}"
        parents.append(
            {
                "id": series_id,
                "title": title,
                "sortTitle": title.lower(),
                "monitored": True,
                "tvdbId": 1_000_000 + series_id,
                "status": "continuing",
            }
        )
        for ep_num in range(1, episodes_per_series + 1):
            air_date = base_date + timedelta(days=leaf_id - 1000)
            leaves.append(
                {
                    "id": leaf_id,
                    "seriesId": series_id,
                    "tvdbId": 2_000_000 + leaf_id,
                    "seriesTitle": title,
                    "title": f"Episode {ep_num:02d}",
                    "seasonNumber": 1,
                    "episodeNumber": ep_num,
                    "absoluteEpisodeNumber": ep_num,
                    "airDate": air_date.date().isoformat(),
                    "airDateUtc": air_date.isoformat().replace("+00:00", "Z"),
                    "series": {
                        "id": series_id,
                        "title": title,
                        "monitored": True,
                    },
                }
            )
            leaf_id += 1

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
        app_name="Sonarr",
        app_version="4.0.17.2952",
        api_prefix="/sonarr/api/v3",
        api_version="v3",
        sort_key_default="episodes.airDateUtc",
        sort_direction_default="ascending",
        parents=parents,
        leaves=leaves,
        missing_ids=missing_ids,
        cutoff_ids=cutoff_ids,
        upgrade_ids=upgrade_ids,
    )


def _library_shape(record: dict[str, Any], data: AppData) -> dict[str, Any]:
    """Return a leaf in the ``/episode`` library shape.

    Real Sonarr returns each episode with ``monitored``, ``hasFile``, and
    a nested ``episodeFile.qualityCutoffNotMet`` flag the engine reads
    during the upgrade pass.
    """
    leaf_id = record["id"]
    has_file = leaf_id not in data.missing_ids
    cutoff_not_met = leaf_id in data.cutoff_ids
    payload = {
        **record,
        "monitored": True,
        "hasFile": has_file,
    }
    payload["episodeFile"] = (
        {"id": leaf_id, "qualityCutoffNotMet": cutoff_not_met} if has_file else None
    )
    return payload


def make_sonarr_router(data: AppData) -> APIRouter:
    router = APIRouter(prefix=data.api_prefix)
    attach_common_routes(router, data)

    @router.get("/wanted/missing")
    async def wanted_missing(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=2000, alias="pageSize"),
        sort_key: str = Query("airDateUtc", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
        include_series: bool = Query(False, alias="includeSeries"),
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
        sort_key: str = Query("airDateUtc", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
        include_series: bool = Query(False, alias="includeSeries"),
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

    @router.get("/series")
    async def get_series() -> list[dict[str, Any]]:
        return data.parents

    @router.get("/episode")
    async def get_episodes(
        series_id: int = Query(..., alias="seriesId"),
        include_episode_file: bool = Query(False, alias="includeEpisodeFile"),
        include_series: bool = Query(False, alias="includeSeries"),
    ) -> list[dict[str, Any]]:
        return [_library_shape(leaf, data) for leaf in data.leaves if leaf["seriesId"] == series_id]

    return router

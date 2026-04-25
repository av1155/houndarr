"""Lidarr mock router.

Parent aggregate is the artist; leaves are albums. Houndarr's upgrade pass
paginates ``/wanted/cutoff`` to build an exclusion set, then walks the
flat ``/album`` library and keeps everything that has a file but is not
cutoff-unmet. The seeder mirrors that contract.
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


def make_lidarr_data(
    *,
    seed: int = 42,
    artist_count: int = 50,
    albums_per_artist: int = 10,
    missing_ratio: float = 0.5,
    cutoff_ratio: float = 0.2,
) -> AppData:
    rng = random.Random(seed)
    parents: list[dict[str, Any]] = []
    leaves: list[dict[str, Any]] = []
    leaf_id = 4000
    base_date = datetime(2005, 1, 1, tzinfo=UTC)
    for artist_id in range(1, artist_count + 1):
        artist_name = f"Mock Lidarr Artist {artist_id:03d}"
        parents.append(
            {
                "id": artist_id,
                "artistName": artist_name,
                "sortName": artist_name.lower(),
                "monitored": True,
                "foreignArtistId": f"mb-artist-{artist_id:06d}",
                "status": "active",
            }
        )
        for album_index in range(1, albums_per_artist + 1):
            release_date = base_date + timedelta(days=leaf_id - 4000)
            album_title = f"Album {album_index:02d}"
            leaves.append(
                {
                    "id": leaf_id,
                    "artistId": artist_id,
                    "title": album_title,
                    "disambiguation": "",
                    "albumType": "Album",
                    "foreignAlbumId": f"mb-album-{leaf_id:06d}",
                    "monitored": True,
                    "releaseDate": release_date.isoformat().replace("+00:00", "Z"),
                    "duration": 2_400_000,
                    "artist": {
                        "id": artist_id,
                        "artistName": artist_name,
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
        app_name="Lidarr",
        app_version="3.1.0.4875",
        api_prefix="/lidarr/api/v1",
        api_version="v1",
        sort_key_default="releaseDate",
        sort_direction_default="ascending",
        parents=parents,
        leaves=leaves,
        missing_ids=missing_ids,
        cutoff_ids=cutoff_ids,
        upgrade_ids=upgrade_ids,
    )


def _library_shape(record: dict[str, Any], data: AppData) -> dict[str, Any]:
    leaf_id = record["id"]
    has_file = leaf_id not in data.missing_ids
    track_file_count = 12 if has_file else 0
    return {
        **record,
        "statistics": {
            "trackFileCount": track_file_count,
            "trackCount": 12,
            "totalTrackCount": 12,
            "sizeOnDisk": 12 * 5_000_000 if has_file else 0,
        },
    }


def make_lidarr_router(data: AppData) -> APIRouter:
    router = APIRouter(prefix=data.api_prefix)
    attach_common_routes(router, data)

    @router.get("/wanted/missing")
    async def wanted_missing(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=2000, alias="pageSize"),
        sort_key: str = Query("releaseDate", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
        include_artist: bool = Query(False, alias="includeArtist"),
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
        sort_key: str = Query("releaseDate", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
        include_artist: bool = Query(False, alias="includeArtist"),
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

    @router.get("/artist")
    async def get_artists() -> list[dict[str, Any]]:
        return data.parents

    @router.get("/album")
    async def get_albums(
        include_artist: bool = Query(False, alias="includeArtist"),
        artist_id: int | None = Query(None, alias="artistId"),
    ) -> list[dict[str, Any]]:
        records = data.leaves
        if artist_id is not None:
            records = [leaf for leaf in records if leaf["artistId"] == artist_id]
        return [_library_shape(leaf, data) for leaf in records]

    return router

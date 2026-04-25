"""Readarr mock router.

Parent aggregate is the author; leaves are books. Mirrors Lidarr's contract
(paginated wanted + flat library walk for upgrades) but uses the v1 API
path with ``includeAuthor`` instead of ``includeArtist``.
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


def make_readarr_data(
    *,
    seed: int = 42,
    author_count: int = 50,
    books_per_author: int = 10,
    missing_ratio: float = 0.5,
    cutoff_ratio: float = 0.2,
) -> AppData:
    rng = random.Random(seed)
    parents: list[dict[str, Any]] = []
    leaves: list[dict[str, Any]] = []
    leaf_id = 5000
    base_date = datetime(2000, 1, 1, tzinfo=UTC)
    for author_id in range(1, author_count + 1):
        author_name = f"Mock Readarr Author {author_id:03d}"
        parents.append(
            {
                "id": author_id,
                "authorName": author_name,
                "sortName": author_name.lower(),
                "monitored": True,
                "foreignAuthorId": f"gr-author-{author_id:06d}",
                "status": "continuing",
            }
        )
        for book_index in range(1, books_per_author + 1):
            release_date = base_date + timedelta(days=leaf_id - 5000)
            book_title = f"Book {book_index:02d}"
            leaves.append(
                {
                    "id": leaf_id,
                    "authorId": author_id,
                    "title": book_title,
                    "authorTitle": author_name,
                    "disambiguation": "",
                    "foreignBookId": f"gr-book-{leaf_id:06d}",
                    "monitored": True,
                    "pageCount": 320,
                    "releaseDate": release_date.isoformat().replace("+00:00", "Z"),
                    "author": {
                        "id": author_id,
                        "authorName": author_name,
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
        app_name="Readarr",
        app_version="0.4.20.129",
        api_prefix="/readarr/api/v1",
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
    book_file_count = 1 if has_file else 0
    return {
        **record,
        "statistics": {
            "bookFileCount": book_file_count,
            "bookCount": 1,
            "totalBookCount": 1,
            "sizeOnDisk": 4_000_000 if has_file else 0,
        },
    }


def make_readarr_router(data: AppData) -> APIRouter:
    router = APIRouter(prefix=data.api_prefix)
    attach_common_routes(router, data)

    @router.get("/wanted/missing")
    async def wanted_missing(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=2000, alias="pageSize"),
        sort_key: str = Query("releaseDate", alias="sortKey"),
        sort_direction: str = Query("ascending", alias="sortDirection"),
        monitored: bool = Query(True),
        include_author: bool = Query(False, alias="includeAuthor"),
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
        include_author: bool = Query(False, alias="includeAuthor"),
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

    @router.get("/author")
    async def get_authors() -> list[dict[str, Any]]:
        return data.parents

    @router.get("/book")
    async def get_books(
        include_author: bool = Query(False, alias="includeAuthor"),
        author_id: int | None = Query(None, alias="authorId"),
    ) -> list[dict[str, Any]]:
        records = data.leaves
        if author_id is not None:
            records = [leaf for leaf in records if leaf["authorId"] == author_id]
        return [_library_shape(leaf, data) for leaf in records]

    return router

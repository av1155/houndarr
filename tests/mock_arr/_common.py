"""Shared helpers used by every per-app router.

The pagination envelope, ping/queue/command handlers, and the partition logic
are identical across all six *arr types. Centralising them keeps each router
file focused on the per-app record shape.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, Body, Query

from tests.mock_arr.store import AppData


def paginate(
    records: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
    sort_key: str,
    sort_direction: str,
) -> dict[str, Any]:
    """Wrap a slice of ``records`` in the *arr pagination envelope.

    Real *arr APIs sort server-side. The mock keeps records in a stable
    seeded order so callers see deterministic pagination; we still echo
    the requested ``sort_key`` / ``sort_direction`` to match the wire shape.
    """
    total = len(records)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    return {
        "page": page,
        "pageSize": page_size,
        "sortKey": sort_key,
        "sortDirection": sort_direction,
        "totalRecords": total,
        "records": records[start:end],
    }


def partition_leaf_ids(
    leaf_ids: list[int],
    *,
    seed: int,
    missing_ratio: float,
    cutoff_ratio: float,
) -> tuple[set[int], set[int], set[int]]:
    """Split a flat list of leaf ids into missing / cutoff / upgrade buckets.

    The ratios apply in order: ``missing_ratio`` first, then ``cutoff_ratio``
    of the remainder, then everything left becomes upgrade-eligible. The
    partition is shuffled deterministically with ``seed`` so the same seed
    always reproduces the same bucket assignment.
    """
    rng = random.Random(seed)
    pool = list(leaf_ids)
    rng.shuffle(pool)
    n = len(pool)
    n_missing = int(n * missing_ratio)
    n_cutoff = int(n * cutoff_ratio)
    missing = set(pool[:n_missing])
    cutoff = set(pool[n_missing : n_missing + n_cutoff])
    upgrade = set(pool[n_missing + n_cutoff :])
    return missing, cutoff, upgrade


def attach_common_routes(router: APIRouter, data: AppData) -> None:
    """Wire ``/system/status``, ``/queue/status``, and ``POST /command``.

    Every *arr app exposes these three endpoints with the same shape, so
    each per-app router gets them via this helper rather than re-declaring.
    The command handler stores every POST so tests can assert dispatch.
    """

    @router.get("/system/status")
    async def system_status() -> dict[str, Any]:
        return {"appName": data.app_name, "version": data.app_version}

    @router.get("/queue/status")
    async def queue_status() -> dict[str, Any]:
        return {
            "totalCount": 0,
            "count": 0,
            "unknownCount": 0,
            "errors": False,
            "warnings": False,
        }

    @router.post("/command")
    async def post_command(
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        data.command_log.entries.append(body)
        return {
            "id": len(data.command_log.entries),
            "name": body.get("name", "Unknown"),
            "status": "queued",
            "queued": "2026-04-25T00:00:00Z",
        }


def standard_pagination_params() -> dict[str, Any]:
    """Return the FastAPI ``Query`` defaults shared by every wanted handler.

    Centralised so individual routers only declare per-app-specific params.
    """
    return {
        "page": Query(1, ge=1),
        "page_size": Query(10, ge=1, le=2000, alias="pageSize"),
        "sort_key": Query(None, alias="sortKey"),
        "sort_direction": Query("ascending", alias="sortDirection"),
    }

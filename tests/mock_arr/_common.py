"""Shared helpers used by every per-app router.

The pagination envelope, ping/queue/command handlers, and the partition logic
are identical across all six *arr types. Centralising them keeps each router
file focused on the per-app record shape.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from typing import Any, cast, get_args

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from tests.mock_arr.store import AppData, WantedFault, WantedFaultOp

logger = logging.getLogger(__name__)


def resolve_sort_key(sort_key: str | None, data: AppData, kind: str) -> str:
    """Return the key the app ends up sorting by, or raise the way it would.

    Real behaviour splits by version.  Builds from late 2024 on carry an
    API-layer allowlist and quietly swap an unlisted key for their own
    default, so a wrong key reads as a healthy 200 that sorts by the wrong
    column.  Older builds hand the key to SQLite and answer 500.

    The mock models both halves.  A key outside ``sort_keys`` fails as on
    the older builds, because a key is only safe if every supported build
    accepts it.  A key inside ``sort_keys`` but outside the allowlist is
    swapped for ``sort_key_default``, as on the newer ones.  With no key at
    all, each app falls back to its ``unsorted_keys`` entry for the kind,
    else to ``sort_key_default``.
    """
    if sort_key is None:
        fallback = data.unsorted_keys.get(kind, data.sort_key_default)
        if fallback is None:
            raise HTTPException(
                status_code=500,
                detail="Object reference not set to an instance of an object.",
            )
        return fallback
    if _rejects(data.sort_keys, sort_key):
        column = sort_key
        if data.sort_key_error_capitalises:
            column = column[:1].upper() + column[1:]
        raise HTTPException(
            status_code=500,
            detail=f"SQL logic error\nno such column: {data.sort_key_table}.{column}",
        )
    if _rejects(data.sort_key_allowlist, sort_key):
        return data.sort_key_default
    return sort_key


def _rejects(keys: frozenset[str], sort_key: str) -> bool:
    return bool(keys) and sort_key.lower() not in {key.lower() for key in keys}


def sort_records(
    records: list[dict[str, Any]], *, field: str | None, descending: bool
) -> list[dict[str, Any]]:
    """Order ``records`` by ``field`` the way SQLite orders a nullable column.

    NULLs come first ascending and last descending.  Ties keep their
    incoming order, which is id order for every router.
    """
    if field is None:
        return list(records)
    dated = [record for record in records if record.get(field) is not None]
    undated = [record for record in records if record.get(field) is None]
    dated.sort(key=lambda record: record[field], reverse=descending)
    return dated + undated if descending else undated + dated


def paginate(
    records: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
    sort_key: str,
    sort_direction: str,
) -> dict[str, Any]:
    """Wrap a slice of already-sorted ``records`` in the *arr pagination envelope.

    ``sort_key`` and ``sort_direction`` are the effective ones, which is
    what the real envelope reports.
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


def wanted_page(
    data: AppData,
    kind: str,
    *,
    page: int,
    page_size: int,
    sort_key: str | None,
    sort_direction: str | None,
) -> dict[str, Any]:
    """Answer one ``/wanted/{kind}`` request the way the real app would.

    Current builds of every app read a missing ``sortDirection`` as
    descending in their paging resource, before any controller default
    applies.

    A fault armed for ``(kind, page, page_size)`` changes the list first.
    With ``o = (page - 1) * page_size`` the offset this page starts at:

    - ``leave``: the item at ``o - 1``, already read on the page before,
      leaves the list, so the item at ``o`` slides back and goes unread.
    - ``leave_unread``: the item at ``o`` leaves before anyone reads it.
    - ``join``: the first upgrade item that sorts at ``o`` or later joins.
    - ``join_before``: the first upgrade item that sorts before ``o``
      joins, pushing the item at ``o - 1`` onto this page a second time.
    - ``leave_join``: ``leave``, then ``join``, so the total ends unchanged.
    - ``tie_split``: this response alone swaps the items at ``o - 1`` and
      ``o``, as a database may order tied rows differently per page.
    - ``leave_after_records``: this page's records come from the list
      before its last item leaves and its total from the list after, as
      when an import lands between the app's records and count queries.
    """
    data.page_log.entries.append((kind, page, page_size))
    effective_key = resolve_sort_key(sort_key, data, kind)
    descending = sort_direction is None or sort_direction.lower() == "descending"
    sort_field = data.sort_fields.get(effective_key.lower())

    def ordered() -> list[dict[str, Any]]:
        wanted_ids = data.missing_ids if kind == "missing" else data.cutoff_ids
        rows = data.cutoff_rows if kind == "cutoff" else {}
        records = [
            leaf
            for leaf in data.leaves
            if leaf["id"] in wanted_ids
            for _ in range(rows.get(leaf["id"], 1))
        ]
        return sort_records(records, field=sort_field, descending=descending)

    served, total = ordered(), None
    fault = data.wanted_faults.get((kind, page, page_size))
    if fault is not None:
        if not fault.sticky:
            del data.wanted_faults[(kind, page, page_size)]
        served, total = _apply_fault(data, kind, fault, page, page_size, ordered)
    envelope = paginate(
        served,
        page=page,
        page_size=page_size,
        sort_key=effective_key,
        sort_direction="descending" if descending else "ascending",
    )
    if total is not None:
        envelope["totalRecords"] = total
    return envelope


_FAULT_OPS = frozenset(get_args(WantedFaultOp.__value__))


def arm_wanted_fault(
    data: AppData,
    kind: str,
    page: int,
    op: str,
    *,
    page_size: int = 250,
    sticky: bool = False,
) -> None:
    """Arm ``op`` for the ``(kind, page, page_size)`` request; see ``wanted_page``.

    Every op but ``leave_after_records`` acts on the page boundary before
    ``page``, so page 1 has none to act on.
    """
    if kind not in {"missing", "cutoff"}:
        raise ValueError(f"unknown wanted kind: {kind}")
    if op not in _FAULT_OPS:
        raise ValueError(f"unknown fault op: {op}")
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    if page < 2 and op != "leave_after_records":
        raise ValueError(f"{op} needs a page boundary before it: page must be 2 or more")
    if page < 1:
        raise ValueError("page must be 1 or more")
    data.wanted_faults[(kind, page, page_size)] = WantedFault(cast(WantedFaultOp, op), sticky)


def _apply_fault(
    data: AppData,
    kind: str,
    fault: WantedFault,
    page: int,
    page_size: int,
    ordered: Callable[[], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int | None]:
    """Change the list per ``fault``; return the rows to page and any total override."""
    before = ordered()
    n_before = len(before)
    offset = (page - 1) * page_size
    wanted = data.missing_ids if kind == "missing" else data.cutoff_ids
    event: dict[str, Any] = {
        "op": fault.op,
        "kind": kind,
        "page": page,
        "page_size": page_size,
        "left": None,
        "joined": None,
        "skipped": None,
        "duplicated": None,
        "n_before": n_before,
    }

    def leave(leaf_id: int) -> None:
        wanted.discard(leaf_id)
        data.upgrade_ids.add(leaf_id)
        event["left"] = leaf_id

    def join(lands: Callable[[int], bool]) -> bool:
        for leaf_id in sorted(data.upgrade_ids):
            wanted.add(leaf_id)
            position = next(i for i, leaf in enumerate(ordered()) if leaf["id"] == leaf_id)
            if lands(position):
                data.upgrade_ids.discard(leaf_id)
                event["joined"] = leaf_id
                return True
            wanted.discard(leaf_id)
        return False

    def at(index: int) -> int | None:
        return before[index]["id"] if 0 <= index < n_before else None

    served, total = before, None
    fired = True
    match fault.op:
        case "leave" | "leave_join":
            fired = offset - 1 < n_before
            if fired:
                leave(before[offset - 1]["id"])
                event["skipped"] = at(offset)
                if fault.op == "leave_join":
                    join(lambda position: position >= offset)
                served = ordered()
        case "leave_unread":
            fired = offset < n_before
            if fired:
                leave(before[offset]["id"])
                served = ordered()
        case "join" | "join_before":
            if fault.op == "join":
                fired = join(lambda position: position >= offset)
            else:
                fired = offset - 1 < n_before and join(lambda position: position < offset)
                event["duplicated"] = at(offset - 1) if fired else None
            served = ordered()
        case "tie_split":
            fired = offset < n_before
            if fired:
                served = list(before)
                served[offset - 1], served[offset] = served[offset], served[offset - 1]
                event["duplicated"], event["skipped"] = at(offset - 1), at(offset)
        case "leave_after_records":
            last = page * page_size - 1
            fired = last < n_before
            if fired:
                leave(before[last]["id"])
                event["skipped"] = at(last + 1)
                total = len(ordered())
    event["n_after"] = len(ordered())
    if fired:
        data.fired_faults.append(event)
    else:
        logger.info("mock fault %s on %s page %d found no item to act on", fault.op, kind, page)
    return served, total


def shuffle_date_fields(
    leaves: list[dict[str, Any]], fields: tuple[str, ...], *, seed: int
) -> None:
    """Deal the leaves' date ``fields`` out again in a seeded random order.

    Seeded dates rise with id, so storage order already equals date order
    and a mock that never sorted would pass any ordering test.  Moving the
    fields together keeps each leaf's dates consistent with each other, and
    a permutation adds no date the default seed lacks.  The separate
    ``Random`` leaves every other draw, and so the default seeds, untouched.
    """
    rows = [tuple(leaf[name] for name in fields) for leaf in leaves]
    random.Random(f"{seed}:dates").shuffle(rows)
    for leaf, row in zip(leaves, rows, strict=True):
        leaf.update(zip(fields, row, strict=True))


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


_QUEUE_LEAF_KEYS: dict[str, str] = {
    "sonarr": "episodeId",
    "whisparr_v2": "episodeId",
    "radarr": "movieId",
    "whisparr_v3": "movieId",
    "lidarr": "albumId",
    "readarr": "bookId",
}


def attach_common_routes(router: APIRouter, data: AppData) -> None:
    """Wire ``/system/status``, the two queue reads, ``/tag``, and ``POST /command``.

    Every *arr app exposes these endpoints with the same shape, so each
    per-app router gets them via this helper rather than re-declaring.
    The command handler stores every POST so tests can assert dispatch;
    ``/queue/details`` reports ``data.queued_ids`` under the app's leaf key.
    """
    leaf_key = _QUEUE_LEAF_KEYS[data.api_prefix.split("/")[1]]

    @router.get("/system/status")
    async def system_status() -> dict[str, Any]:
        return {"appName": data.app_name, "version": data.app_version}

    @router.get("/queue/status")
    async def queue_status() -> dict[str, Any]:
        return {
            "totalCount": len(data.queued_ids),
            "count": len(data.queued_ids),
            "unknownCount": 0,
            "errors": False,
            "warnings": False,
        }

    @router.get("/queue/details")
    async def queue_details() -> list[dict[str, Any]]:
        data.queue_detail_requests += 1
        return [
            {
                "id": 900_000 + leaf_id,
                leaf_key: leaf_id,
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "trackedDownloadState": "downloading",
            }
            for leaf_id in sorted(data.queued_ids)
        ]

    @router.get("/tag", response_model=None)
    async def get_tags() -> list[dict[str, Any]] | Response:
        data.tag_requests += 1
        if data.tag_status != 200:
            raise HTTPException(status_code=data.tag_status)
        if data.tag_html:
            return Response("<html>Sign in</html>", media_type="text/html")
        return data.tags

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

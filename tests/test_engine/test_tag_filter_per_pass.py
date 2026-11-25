"""Tag filters through full search cycles, pass by pass (issue #833)."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import httpx
import pytest
import respx

from houndarr.database import get_db
from houndarr.engine.search_loop import run_instance_search
from houndarr.errors import ClientTransportError
from houndarr.services.instances import Instance, InstanceType, SearchOrder, TagFilterPolicy

from .conftest import (
    _EPISODE_RECORD,
    _MOVIE_RECORD,
    MASTER_KEY,
    RADARR_URL,
    SONARR_URL,
    get_log_rows,
    make_instance,
)

_TAG_500 = "get_tags: HTTP 500 from /api/v3/tag"


def _radarr_all_passes(tag_filter: TagFilterPolicy) -> Instance:
    return dataclasses.replace(
        make_instance(
            instance_id=2,
            itype=InstanceType.radarr,
            cutoff_enabled=True,
            upgrade_enabled=True,
            upgrade_cooldown_days=7,
            upgrade_hourly_cap=5,
        ),
        tag_filter=tag_filter,
    )


def _serve_empty_radarr() -> dict[str, respx.Route]:
    """Serve empty wanted lists, an empty library and one tag."""
    return {
        "missing": respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json={"records": []})
        ),
        "cutoff": respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
            return_value=httpx.Response(200, json={"records": []})
        ),
        "upgrade": respx.get(f"{RADARR_URL}/api/v3/movie").mock(
            return_value=httpx.Response(200, json=[])
        ),
        "tag": respx.get(f"{RADARR_URL}/api/v3/tag").mock(
            return_value=httpx.Response(200, json=[{"id": 3, "label": "skip"}])
        ),
    }


def _page(records: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200, json={"page": 1, "pageSize": 10, "totalRecords": len(records), "records": records}
    )


def _episode(episode_id: int, *, number: int, series_tags: list[int]) -> dict[str, Any]:
    return {
        **_EPISODE_RECORD,
        "id": episode_id,
        "episodeNumber": number,
        "series": {"title": "My Show", "tags": series_tags},
    }


def _movie(movie_id: int, tags: list[int]) -> dict[str, Any]:
    return {**_MOVIE_RECORD, "id": movie_id, "title": f"Movie {movie_id}", "tags": tags}


def _library_movie(movie_id: int, tags: list[int]) -> dict[str, Any]:
    return {
        "id": movie_id,
        "title": f"Movie {movie_id}",
        "year": 2023,
        "monitored": True,
        "hasFile": True,
        "movieFile": {"qualityCutoffNotMet": False},
        "tags": tags,
    }


def _searched_ids(route: respx.Route, key: str) -> list[int]:
    return [i for call in route.calls for i in json.loads(call.request.content)[key]]


def _rows_for(rows: list[dict[str, Any]], kind: str) -> list[tuple[str, int, str | None]]:
    return [(r["action"], r["item_id"], r["reason"]) for r in rows if r["search_kind"] == kind]


@pytest.mark.pinning()
@pytest.mark.asyncio()
@respx.mock
async def test_no_tag_get_without_labels_on_any_pass(seeded_instances: None) -> None:
    """An instance with no tag labels never requests ``/tag``."""
    routes = _serve_empty_radarr()

    await run_instance_search(_radarr_all_passes(TagFilterPolicy()), MASTER_KEY)

    assert routes["missing"].called
    assert routes["cutoff"].called
    assert routes["upgrade"].called
    assert not routes["tag"].called


@pytest.mark.pinning()
@pytest.mark.asyncio()
@respx.mock
async def test_tag_get_once_per_cycle_instance_wide_labels(seeded_instances: None) -> None:
    """Instance-wide labels cost one ``/tag`` request per cycle, whatever the pass count."""
    routes = _serve_empty_radarr()

    await run_instance_search(
        _radarr_all_passes(TagFilterPolicy(include=(), exclude=("skip",))), MASTER_KEY
    )

    assert routes["missing"].called
    assert routes["cutoff"].called
    assert routes["upgrade"].called
    assert routes["tag"].call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_tag_get_once_per_cycle_with_own_labels(seeded_instances: None) -> None:
    routes = _serve_empty_radarr()
    policy = TagFilterPolicy(
        missing_exclude=("skip",), cutoff_exclude=("skip",), upgrade_exclude=("skip",)
    )

    await run_instance_search(_radarr_all_passes(policy), MASTER_KEY)

    assert routes["upgrade"].called
    assert routes["tag"].call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_exclude_skips_series_while_missing_pass_still_searches(
    seeded_instances: None,
) -> None:
    """The #833 case: keep a series out of cutoff searches, still fill its gaps."""
    tag_route = respx.get(f"{SONARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(200, json=[{"id": 7, "label": "no-upgrade"}])
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=_page([_episode(101, number=1, series_tags=[7])])
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=_page([_episode(102, number=2, series_tags=[7])])
    )
    command = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(cutoff_enabled=True),
        tag_filter=TagFilterPolicy(cutoff_exclude=("no-upgrade",)),
    )

    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    assert _searched_ids(command, "episodeIds") == [101]
    rows = await get_log_rows()
    assert _rows_for(rows, "missing") == [("searched", 101, None)]
    assert _rows_for(rows, "cutoff") == [("skipped", 102, "tag filter (excluded tag)")]
    assert tag_route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_missing_exclude_skips_only_the_missing_pass(seeded_instances: None) -> None:
    respx.get(f"{SONARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(200, json=[{"id": 7, "label": "no-backfill"}])
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=_page([_episode(101, number=1, series_tags=[7])])
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=_page([_episode(102, number=2, series_tags=[7])])
    )
    command = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(cutoff_enabled=True),
        tag_filter=TagFilterPolicy(missing_exclude=("no-backfill",)),
    )

    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    assert _searched_ids(command, "episodeIds") == [102]
    rows = await get_log_rows()
    assert _rows_for(rows, "missing") == [("skipped", 101, "tag filter (excluded tag)")]
    assert _rows_for(rows, "cutoff") == [("searched", 102, None)]


@pytest.mark.asyncio()
@respx.mock
async def test_upgrade_exclude_skips_library_movie_while_missing_pass_still_searches(
    seeded_instances: None,
) -> None:
    respx.get(f"{RADARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(200, json=[{"id": 7, "label": "no-upgrade"}])
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(return_value=_page([_movie(201, [7])]))
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[_library_movie(202, [7])])
    )
    command = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(
            instance_id=2,
            itype=InstanceType.radarr,
            upgrade_enabled=True,
            upgrade_cooldown_days=7,
            upgrade_hourly_cap=5,
        ),
        tag_filter=TagFilterPolicy(upgrade_exclude=("no-upgrade",)),
    )

    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    assert _searched_ids(command, "movieIds") == [201]
    rows = await get_log_rows()
    assert _rows_for(rows, "upgrade") == [("skipped", 202, "tag filter (excluded tag)")]


@pytest.mark.asyncio()
@respx.mock
async def test_instance_wide_exclude_still_applies_on_pass_with_own_exclude(
    seeded_instances: None,
) -> None:
    respx.get(f"{RADARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(
            200, json=[{"id": 3, "label": "skip"}, {"id": 7, "label": "no-upgrade"}]
        )
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=_page([_movie(301, [3]), _movie(302, [7]), _movie(303, [])])
    )
    command = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(
            instance_id=2, itype=InstanceType.radarr, missing_enabled=False, cutoff_enabled=True
        ),
        tag_filter=TagFilterPolicy(exclude=("skip",), cutoff_exclude=("no-upgrade",)),
    )

    await run_instance_search(instance, MASTER_KEY)

    assert _searched_ids(command, "movieIds") == [303]
    rows = await get_log_rows()
    assert _rows_for(rows, "cutoff") == [
        ("skipped", 301, "tag filter (excluded tag)"),
        ("skipped", 302, "tag filter (excluded tag)"),
        ("searched", 303, None),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_tag_fetch_failure_skips_labelled_pass_and_runs_unlabelled_pass(
    seeded_instances: None,
) -> None:
    respx.get(f"{SONARR_URL}/api/v3/tag").mock(return_value=httpx.Response(500))
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=_page([_episode(101, number=1, series_tags=[7])])
    )
    cutoff_route = respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=_page([_episode(102, number=2, series_tags=[])])
    )
    command = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(cutoff_enabled=True),
        tag_filter=TagFilterPolicy(cutoff_exclude=("no-upgrade",)),
    )

    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    assert not cutoff_route.called
    assert _searched_ids(command, "episodeIds") == [101]
    rows = await get_log_rows()
    info = [r for r in rows if r["action"] == "info"]
    assert [(r["reason"], r["message"]) for r in info] == [
        (
            "tag filter (fetch failed)",
            f"tag filter unavailable, skipping the cutoff pass this cycle: {_TAG_500}",
        )
    ]
    assert _rows_for(rows, "cutoff") == []


@pytest.mark.parametrize("trigger", ["scheduled", "run_now"])
@pytest.mark.asyncio()
@respx.mock
async def test_tag_fetch_failure_with_instance_wide_label_skips_every_pass(
    seeded_instances: None, trigger: str
) -> None:
    """Fail-closed replaces #644's fail-open: no pass runs unfiltered."""
    routes = _serve_empty_radarr()
    routes["tag"].mock(return_value=httpx.Response(500))
    command = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    count = await run_instance_search(
        _radarr_all_passes(TagFilterPolicy(include=("1080p",))),
        MASTER_KEY,
        cycle_trigger=trigger,
    )

    assert count == 0
    assert not routes["missing"].called
    assert not routes["cutoff"].called
    assert not routes["upgrade"].called
    assert not command.called
    rows = await get_log_rows()
    assert [(r["action"], r["reason"], r["message"]) for r in rows] == [
        (
            "info",
            "tag filter (fetch failed)",
            "tag filter unavailable, skipping the missing, cutoff, upgrade passes this cycle: "
            f"{_TAG_500}",
        )
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_tag_non_json_body_skips_labelled_pass(seeded_instances: None) -> None:
    """A proxy login page on ``/tag`` fails closed instead of aborting the cycle."""
    respx.get(f"{SONARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(200, text="<html>Sign in</html>")
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=_page([_episode(101, number=1, series_tags=[])])
    )
    cutoff_route = respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(return_value=_page([]))
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(cutoff_enabled=True),
        tag_filter=TagFilterPolicy(cutoff_exclude=("no-upgrade",)),
    )

    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    assert not cutoff_route.called
    rows = await get_log_rows()
    assert [r["message"] for r in rows if r["action"] == "info"] == [
        "tag filter unavailable, skipping the cutoff pass this cycle: "
        "get_tags: non-JSON body from /api/v3/tag"
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_tag_transport_error_propagates_from_cycle(seeded_instances: None) -> None:
    """An unreachable *arr reaches the supervisor's reconnect path, not a skip row."""
    respx.get(f"{RADARR_URL}/api/v3/tag").mock(side_effect=httpx.ConnectError("refused"))
    missing_route = respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(return_value=_page([]))

    with pytest.raises(ClientTransportError):
        await run_instance_search(
            _radarr_all_passes(TagFilterPolicy(exclude=("skip",))), MASTER_KEY
        )

    assert not missing_route.called
    assert await get_log_rows() == []


@pytest.mark.asyncio()
@respx.mock
async def test_skipped_pass_keeps_its_page_offset(seeded_instances: None) -> None:
    async with get_db() as conn:
        await conn.execute("UPDATE instances SET cutoff_page_offset = 3 WHERE id = 1")
        await conn.commit()
    respx.get(f"{SONARR_URL}/api/v3/tag").mock(return_value=httpx.Response(500))
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(return_value=_page([]))
    instance = dataclasses.replace(
        make_instance(
            cutoff_enabled=True, cutoff_page_offset=3, search_order=SearchOrder.chronological
        ),
        tag_filter=TagFilterPolicy(cutoff_exclude=("no-upgrade",)),
    )

    await run_instance_search(instance, MASTER_KEY)

    async with get_db() as conn:
        async with conn.execute("SELECT cutoff_page_offset FROM instances WHERE id = 1") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 3


@pytest.mark.asyncio()
@respx.mock
async def test_own_label_on_disabled_pass_does_not_fetch_tags(seeded_instances: None) -> None:
    tag_route = respx.get(f"{RADARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(200, json=[{"id": 7, "label": "no-upgrade"}])
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(return_value=_page([_movie(201, [7])]))
    command = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    instance = dataclasses.replace(
        make_instance(instance_id=2, itype=InstanceType.radarr),
        tag_filter=TagFilterPolicy(upgrade_exclude=("no-upgrade",)),
    )

    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    assert _searched_ids(command, "movieIds") == [201]
    assert not tag_route.called

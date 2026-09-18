"""Interactions between the gates a candidate crosses on one search pass.

The release gate, the context-mode group dedup, the cooldown / retry
block and the download-queue gate each defer or hand back work for the
others.  Each of them is covered on its own in the file named after it;
what is pinned here is what the combination writes to ``search_log``
when more than one fires on records of the same parent in a single pass.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from houndarr.engine.adapters.sonarr import _season_item_id
from houndarr.engine.search_loop import (
    _QUEUE_FETCH_FAILED_REASON,
    _QUEUED_REASON,
    run_instance_search,
)
from houndarr.routes._templates import get_templates
from houndarr.services.cooldown import record_search
from houndarr.services.instances import Instance, SonarrSearchMode
from houndarr.services.log_query import instance_accent_by_name, query_logs
from tests.conftest import serve_download_queue

from .conftest import (
    _EPISODE_RECORD,
    _MOVIE_RECORD,
    MASTER_KEY,
    RADARR_URL,
    SONARR_URL,
    get_log_rows,
    insert_search_log_row,
)
from .test_download_queue_skip import (
    _commands,
    _library_movie,
    _mock_command,
    _mock_radarr_missing,
    _movie,
    _queue_route,
    _radarr,
    _wanted_pages,
)
from .test_release_timing_rows import _NOW, _episode, _freeze_now, _sonarr

_COLLIDING_ID = 201
_QUEUED_IDS = list(range(401, 409))
_SEARCHABLE_IDS = [409, 410]


def _mock_wanted_by_page(pages: dict[int, list[dict[str, Any]]]) -> None:
    """Serve a different wanted page per page number, which one record list cannot."""

    def respond(request: httpx.Request) -> httpx.Response:
        records = pages.get(int(request.url.params.get("page", "1")), [])
        return httpx.Response(
            200,
            json={
                "page": int(request.url.params.get("page", "1")),
                "pageSize": int(request.url.params.get("pageSize", "10")),
                "totalRecords": sum(len(chunk) for chunk in pages.values()),
                "records": records,
            },
        )

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(side_effect=respond)


def _season_episode(episode_id: int, *, season: int, air_at: datetime) -> dict[str, Any]:
    return {**_episode(episode_id, air_at, 1), "seasonNumber": season}


def _render_log_rows(rows: list[dict[str, Any]], accents: dict[str, str]) -> str:
    """Render the Logs partial through the environment the routes themselves use."""
    return (
        get_templates()
        .env.get_template("partials/log_rows.html")
        .render(rows=rows, limit=50, instance_accent_by_name=accents)
    )


def _advance(instance: Instance, offset: int) -> Instance:
    """Feed a persisted cursor back in the way the supervisor's reload would."""
    return replace(instance, upgrade=replace(instance.upgrade, upgrade_item_offset=offset))


# ---------------------------------------------------------------------------
# Release gate meets group dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_three_unreleased_records_of_one_season_each_write_a_row_every_cycle(
    seeded_instances: None,
) -> None:
    """Season mode holds one release-gate row per blocked record, and repeats it each cycle."""
    now = datetime.now(UTC)
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages(
            [
                _episode(111, now + timedelta(minutes=5), 1),
                _episode(112, now + timedelta(days=1), 2),
                _episode(113, now + timedelta(days=2), 3),
            ]
        ),
    )
    command_route = _mock_command(SONARR_URL)
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    assert await run_instance_search(inst, MASTER_KEY) == 0
    assert not command_route.called

    parent_id = _season_item_id(55, 1)
    first_cycle = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in first_cycle] == [
        ("skipped", parent_id, "not yet released"),
    ] * 3

    assert await run_instance_search(inst, MASTER_KEY) == 0
    assert not command_route.called
    assert len(await get_log_rows()) == 6


@pytest.mark.asyncio()
@respx.mock
async def test_a_season_reached_at_its_blocked_record_first_searches_once_then_cools_down(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page offset reaches the blocked record first; the season still pays one search.

    A wanted page sorts ascending, so a record this host still reads as
    unreleased can only be reached before a released one of the same
    season across pages: the offset starts the pass on the blocked
    record's page and the wrap back to page 1 finds the released one.
    The held row has to be dropped on the sibling that arrives later in
    the pass, and the season then sits on its cooldown instead of being
    re-searched every cycle (#782).
    """
    _freeze_now(monkeypatch)
    blocked = _episode(111, _NOW + timedelta(minutes=5), 11)
    released = _episode(101, _NOW - timedelta(days=30), 1)
    _mock_wanted_by_page({1: [released], 2: [blocked], 3: []})
    command_route = _mock_command(SONARR_URL)
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context, missing_page_offset=2)

    assert await run_instance_search(inst, MASTER_KEY) == 1

    parent = _season_item_id(55, 1)
    rows = await get_log_rows()
    assert [r["item_id"] for r in rows if r["action"] == "searched"] == [parent]
    assert not any(r["reason"] == "not yet released" for r in rows)
    assert _commands(command_route) == [
        {"name": "SeasonSearch", "seriesId": 55, "seasonNumber": 1},
    ]

    assert await run_instance_search(inst, MASTER_KEY) == 0

    assert command_route.call_count == 1
    rows = await get_log_rows()
    assert not any(r["reason"] == "not yet released" for r in rows)
    assert "on cooldown (7d)" in [
        r["reason"] for r in rows if r["action"] == "skipped" and r["item_id"] == parent
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_the_cap_that_stops_the_pass_still_drops_a_siblings_held_release_row(
    seeded_instances: None,
) -> None:
    """The hourly cap ending a pass still lets the flush settle the held row."""
    await insert_search_log_row(
        instance_id=1,
        item_id=_season_item_id(56, 1),
        item_type="episode",
        search_kind="missing",
        action="searched",
    )
    _mock_wanted_by_page(
        {
            2: [_episode(111, _NOW + timedelta(minutes=5), 11)],
            1: [_episode(101, _NOW - timedelta(days=30), 1)],
        }
    )
    command_route = _mock_command(SONARR_URL)
    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        hourly_cap=1,
        missing_page_offset=2,
    )

    assert await run_instance_search(inst, MASTER_KEY) == 0

    assert command_route.call_count == 0
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows[1:]] == [
        ("skipped", _season_item_id(55, 1), "hourly limit reached (1/hr)")
    ]


# ---------------------------------------------------------------------------
# Group dedup meets the download queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_the_record_that_searched_a_season_drops_both_of_its_siblings_rows(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One sibling clearing the gates leaves no queue row and no release row behind."""
    _freeze_now(monkeypatch)
    downloading = _episode(101, _NOW - timedelta(days=30), 1)
    eligible = _episode(102, _NOW - timedelta(days=20), 2)
    still_future_here = _episode(111, _NOW + timedelta(minutes=5), 11)
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages([downloading, eligible, still_future_here]),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": 101}])

    inst = _sonarr(batch_size=2, sonarr_search_mode=SonarrSearchMode.season_context)
    assert await run_instance_search(inst, MASTER_KEY) == 1

    assert _commands(command_route) == [
        {"name": "SeasonSearch", "seriesId": 55, "seasonNumber": 1},
    ]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows] == [
        ("searched", _season_item_id(55, 1), None),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_a_season_whose_only_clearing_record_is_downloading_reports_the_download(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The season logs the download it waits on, not the block its other record hit."""
    _freeze_now(monkeypatch)
    downloading = _episode(101, _NOW - timedelta(days=30), 1)
    still_future_here = _episode(111, _NOW + timedelta(minutes=5), 11)
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages([downloading, still_future_here]),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"id": 900101, "episodeId": 101}])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    assert await run_instance_search(inst, MASTER_KEY) == 0

    assert command_route.call_count == 0
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows] == [
        ("skipped", _season_item_id(55, 1), _QUEUED_REASON),
    ]
    assert not any(r["reason"] == "not yet released" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_a_season_blocked_by_grace_and_by_the_queue_logs_both_reasons(
    seeded_instances: None,
) -> None:
    """Season mode writes the grace row during the pass and the queue row at flush."""
    downloading = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages([downloading, in_grace]),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": 101}])

    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)
    assert await run_instance_search(inst, MASTER_KEY) == 0

    assert command_route.call_count == 0
    parent = _season_item_id(55, 1)
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows] == [
        ("skipped", parent, "post-release grace (6h)"),
        ("skipped", parent, _QUEUED_REASON),
    ]


# ---------------------------------------------------------------------------
# Whole cycles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_a_cycle_blocked_by_three_gates_names_each_one_and_buckets_none_as_other(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three seasons, three gates: every reason reaches the cycle summary by name."""
    _freeze_now(monkeypatch)
    await record_search(1, _season_item_id(55, 3), "episode")
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages(
            [
                _season_episode(301, season=3, air_at=_NOW - timedelta(days=30)),
                _season_episode(101, season=1, air_at=_NOW - timedelta(days=20)),
                _season_episode(201, season=2, air_at=_NOW + timedelta(minutes=5)),
            ]
        ),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": 101}])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    assert await run_instance_search(inst, MASTER_KEY) == 0

    assert not command_route.called
    assert [(r["action"], r["item_id"], r["reason"]) for r in await get_log_rows()] == [
        ("skipped", _season_item_id(55, 3), "on cooldown (7d)"),
        ("skipped", _season_item_id(55, 1), _QUEUED_REASON),
        ("skipped", _season_item_id(55, 2), "not yet released"),
    ]

    html = _render_log_rows(await query_logs(limit=50), await instance_accent_by_name())
    assert "<strong>3</strong> items" in html
    assert "1 on cooldown" in html
    assert "1 not yet released" in html
    assert f"1 {_QUEUED_REASON}" in html
    assert " other" not in html


@pytest.mark.asyncio()
@respx.mock
async def test_two_instances_queued_on_the_same_item_id_each_write_their_own_skip_row(
    seeded_instances: None,
) -> None:
    """The queue skip throttle is keyed per instance, so a shared item id logs twice."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages([{**_EPISODE_RECORD, "id": _COLLIDING_ID}]),
    )
    _mock_radarr_missing([{**_MOVIE_RECORD, "id": _COLLIDING_ID}])
    sonarr_commands = _mock_command(SONARR_URL)
    radarr_commands = _mock_command(RADARR_URL)

    # Both cycles run in one test body so they share the skip-log cache.
    serve_download_queue([{"episodeId": _COLLIDING_ID}])
    assert await run_instance_search(_sonarr(), MASTER_KEY) == 0
    serve_download_queue([{"movieId": _COLLIDING_ID}])
    assert await run_instance_search(_radarr(), MASTER_KEY) == 0

    assert sonarr_commands.call_count == 0
    assert radarr_commands.call_count == 0
    rows = await get_log_rows()
    assert [(r["instance_id"], r["item_id"], r["action"], r["reason"]) for r in rows] == [
        (1, _COLLIDING_ID, "skipped", _QUEUED_REASON),
        (2, _COLLIDING_ID, "skipped", _QUEUED_REASON),
    ]


@pytest.mark.asyncio()
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_a_permanently_queued_head_of_the_upgrade_pool_does_not_starve_its_tail(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """A block of queued items at the cursor still lets the cursor and the pass move on."""
    library: list[dict[str, Any]] = [
        _library_movie(movie_id) for movie_id in [*_QUEUED_IDS, *_SEARCHABLE_IDS]
    ]
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=library),
    )
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": movie_id} for movie_id in _QUEUED_IDS])

    instance = _radarr(
        batch_size=0,
        upgrade_enabled=True,
        upgrade_batch_size=1,
        upgrade_item_offset=0,
        upgrade_hourly_cap=50,
        upgrade_cooldown_days=90,
    )

    persisted: list[int] = []
    for _ in range(4):
        await run_instance_search(instance, MASTER_KEY)
        offset = int(mock_update.await_args.kwargs["upgrade_item_offset"])
        persisted.append(offset)
        instance = _advance(instance, offset)

    dispatched = [
        movie_id
        for call in command_route.calls
        for movie_id in json.loads(call.request.content)["movieIds"]
    ]
    assert dispatched == _SEARCHABLE_IDS
    assert persisted == [1, 3, 5, 7]

    rows = await get_log_rows()
    searched_ids = [r["item_id"] for r in rows if r["action"] == "searched"]
    assert searched_ids == _SEARCHABLE_IDS
    assert {r["reason"] for r in rows if r["action"] == "skipped"} == {
        _QUEUED_REASON,
        "on upgrade cooldown (90d)",
    }


@pytest.mark.asyncio()
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_a_failing_queue_read_stays_failed_for_the_whole_cycle(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    """One failed queue read serves all three passes: no pass retries the fetch."""
    _mock_radarr_missing([_movie(201), _movie(202)])
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(side_effect=_wanted_pages([_movie(301)]))
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[_library_movie(401)]),
    )
    command_route = _mock_command(RADARR_URL)
    _queue_route().mock(return_value=httpx.Response(500))

    instance = _radarr(
        batch_size=2,
        cutoff_enabled=True,
        cutoff_hourly_cap=5,
        upgrade_enabled=True,
        upgrade_hourly_cap=5,
    )

    assert await run_instance_search(instance, MASTER_KEY) == 4

    # The info row is throttled for six hours, so only the call count catches a refetch.
    assert _queue_route().call_count == 1
    assert command_route.call_count == 4
    rows = await get_log_rows()
    assert [(r["reason"], r["item_id"]) for r in rows if r["action"] == "info"] == [
        (_QUEUE_FETCH_FAILED_REASON, None),
    ]
    assert [r["action"] for r in rows if r["action"] != "info"] == ["searched"] * 4

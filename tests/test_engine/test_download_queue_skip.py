"""Per-item download-queue skip in the search engine (issue #765)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from houndarr.database import get_db
from houndarr.engine.adapters.lidarr import _artist_item_id
from houndarr.engine.adapters.sonarr import _season_item_id
from houndarr.engine.candidates import SearchCandidate
from houndarr.engine.search_loop import _download_queue_lookup, run_instance_search
from houndarr.services.cooldown import record_search
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    SonarrSearchMode,
)
from tests.conftest import QUEUE_DETAILS_ROUTE, serve_download_queue

from .conftest import (
    _ALBUM_RECORD,
    _EPISODE_RECORD,
    _MISSING_LIDARR,
    _MISSING_RADARR,
    _MISSING_READARR,
    _MISSING_SONARR,
    _MISSING_WHISPARR_V2,
    _MOVIE_RECORD,
    LIDARR_URL,
    MASTER_KEY,
    RADARR_URL,
    READARR_URL,
    SONARR_URL,
    WHISPARR_V2_URL,
    WHISPARR_V3_URL,
    get_log_rows,
    make_instance,
    seed_release_timing_retry,
)

_QUEUED_REASON = "already in download queue"
_EMPTY_PAGE: dict[str, Any] = {"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}


def _wanted_pages(records: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    """Serve *records* through ``/wanted`` pagination like a real *arr."""

    def respond(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        size = int(request.url.params.get("pageSize", "10"))
        chunk = records[(page - 1) * size : page * size]
        return httpx.Response(
            200,
            json={"page": page, "pageSize": size, "totalRecords": len(records), "records": chunk},
        )

    return respond


def _movie(movie_id: int) -> dict[str, Any]:
    return {**_MOVIE_RECORD, "id": movie_id, "title": f"Movie {movie_id}"}


def _library_movie(movie_id: int) -> dict[str, Any]:
    return {
        "id": movie_id,
        "title": f"Movie {movie_id}",
        "year": 2023,
        "monitored": True,
        "hasFile": True,
        "movieFile": {"qualityCutoffNotMet": False},
        "inCinemas": "2023-01-01T00:00:00Z",
    }


def _episode(episode_id: int, *, season: int = 1, number: int = 1) -> dict[str, Any]:
    return {
        **_EPISODE_RECORD,
        "id": episode_id,
        "seasonNumber": season,
        "episodeNumber": number,
    }


def _library_episode(episode_id: int, *, season: int = 1, number: int = 1) -> dict[str, Any]:
    return {
        "id": episode_id,
        "seriesId": 55,
        "series": {"id": 55, "title": "My Show"},
        "title": f"Episode {number}",
        "seasonNumber": season,
        "episodeNumber": number,
        "monitored": True,
        "hasFile": True,
        "episodeFile": {"qualityCutoffNotMet": False},
    }


def _radarr(**overrides: Any) -> Any:
    return make_instance(instance_id=2, itype=InstanceType.radarr, **overrides)


def _sonarr(**overrides: Any) -> Any:
    return make_instance(instance_id=1, itype=InstanceType.sonarr, **overrides)


def _mock_radarr_missing(records: list[dict[str, Any]]) -> None:
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(side_effect=_wanted_pages(records))


def _mock_command(url: str, api: str = "v3") -> respx.Route:
    return respx.post(f"{url}/api/{api}/command").mock(
        return_value=httpx.Response(201, json={"id": 1}),
    )


def _commands(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in route.calls]


def _queue_route() -> respx.Route:
    return respx.routes[QUEUE_DETAILS_ROUTE]


async def _cooldown_rows() -> list[tuple[int, int, str]]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT instance_id, item_id, item_type FROM cooldowns ORDER BY item_id"
        ) as cur:
            rows = await cur.fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------------------------------------------------------------------------
# Missing pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_missing_movie_already_downloading_is_not_searched(
    seeded_instances: None,
) -> None:
    """A wanted movie with a download in the Radarr queue is skipped, not searched."""
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RADARR),
    )
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201, "status": "downloading"}])

    searched = await run_instance_search(_radarr(), MASTER_KEY)

    assert searched == 0
    assert command_route.call_count == 0
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["search_kind"], r["reason"]) for r in rows] == [
        ("skipped", 201, "missing", _QUEUED_REASON),
    ]
    assert rows[0]["item_label"] == "My Movie (2023)"


_APP_CASES: list[tuple[int, InstanceType, str, str, dict[str, Any], str, int]] = [
    (1, InstanceType.sonarr, SONARR_URL, "v3", _MISSING_SONARR, "episodeId", 101),
    (2, InstanceType.radarr, RADARR_URL, "v3", _MISSING_RADARR, "movieId", 201),
    (3, InstanceType.lidarr, LIDARR_URL, "v1", _MISSING_LIDARR, "albumId", 301),
    (4, InstanceType.readarr, READARR_URL, "v1", _MISSING_READARR, "bookId", 401),
    (5, InstanceType.whisparr_v2, WHISPARR_V2_URL, "v3", _MISSING_WHISPARR_V2, "episodeId", 501),
]


@pytest.mark.parametrize(
    ("instance_id", "itype", "url", "api", "missing", "leaf_key", "leaf_id"), _APP_CASES
)
@pytest.mark.asyncio()
@respx.mock
async def test_every_wanted_app_skips_its_queued_leaf(
    seeded_instances: None,
    instance_id: int,
    itype: InstanceType,
    url: str,
    api: str,
    missing: dict[str, Any],
    leaf_key: str,
    leaf_id: int,
) -> None:
    """Each /wanted app matches the queue on its own leaf id field."""
    respx.get(f"{url}/api/{api}/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing)
    )
    command_route = _mock_command(url, api)
    serve_download_queue([{leaf_key: leaf_id}])

    instance = make_instance(instance_id=instance_id, itype=itype)
    assert await run_instance_search(instance, MASTER_KEY) == 0

    assert command_route.call_count == 0
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows] == [
        ("skipped", leaf_id, _QUEUED_REASON),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_v3_skips_queued_movie(seeded_instances: None) -> None:
    """Whisparr v3 builds its wanted list from /movie and matches the queue on movieId."""
    scene = {
        "id": 601,
        "title": "Scene",
        "year": 2023,
        "status": "released",
        "isAvailable": True,
        "monitored": True,
        "hasFile": False,
        "releaseDate": "2023-02-01T00:00:00Z",
    }
    respx.get(f"{WHISPARR_V3_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[scene]),
    )
    command_route = _mock_command(WHISPARR_V3_URL)
    serve_download_queue([{"movieId": 601}])

    instance = make_instance(instance_id=6, itype=InstanceType.whisparr_v3, url=WHISPARR_V3_URL)
    assert await run_instance_search(instance, MASTER_KEY) == 0
    assert command_route.call_count == 0
    rows = await get_log_rows()
    assert [(r["item_id"], r["reason"]) for r in rows] == [(601, _QUEUED_REASON)]


@pytest.mark.asyncio()
@respx.mock
async def test_batch_fills_with_the_next_item_that_is_not_queued(
    seeded_instances: None,
) -> None:
    """A queued item frees its batch slot for the next eligible item."""
    _mock_radarr_missing([_movie(201), _movie(202), _movie(203)])
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201}])

    searched = await run_instance_search(_radarr(batch_size=1), MASTER_KEY)

    assert searched == 1
    assert _commands(command_route) == [{"name": "MoviesSearch", "movieIds": [202]}]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"]) for r in rows] == [
        ("skipped", 201),
        ("searched", 202),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_queued_items_do_not_consume_the_scan_budget(
    seeded_instances: None,
) -> None:
    """Thirty queued movies ahead of an eligible one still let the pass reach it.

    batch_size=1 gives a missing scan budget of 24 and pages of 10; if queued
    skips counted against the budget the pass would stop on page three.
    """
    queued = [_movie(1000 + i) for i in range(30)]
    _mock_radarr_missing([*queued, _movie(2000)])
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": m["id"]} for m in queued])

    searched = await run_instance_search(_radarr(batch_size=1), MASTER_KEY)

    assert searched == 1
    assert _commands(command_route) == [{"name": "MoviesSearch", "movieIds": [2000]}]


@pytest.mark.asyncio()
@respx.mock
async def test_queued_skip_does_not_count_against_the_hourly_cap(
    seeded_instances: None,
) -> None:
    """With a cap of one, the queued item does not use up the hour's only search."""
    _mock_radarr_missing([_movie(201), _movie(202), _movie(203)])
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201}])

    searched = await run_instance_search(_radarr(batch_size=5, hourly_cap=1), MASTER_KEY)

    assert searched == 1
    assert _commands(command_route) == [{"name": "MoviesSearch", "movieIds": [202]}]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows] == [
        ("skipped", 201, _QUEUED_REASON),
        ("searched", 202, None),
        ("skipped", 203, "hourly limit reached (1/hr)"),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_queued_item_is_searched_once_it_leaves_the_queue(
    seeded_instances: None,
) -> None:
    """The skip records no cooldown, so the next cycle searches the item normally."""
    _mock_radarr_missing([_movie(201)])
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201}])

    assert await run_instance_search(_radarr(), MASTER_KEY) == 0
    assert await _cooldown_rows() == []

    serve_download_queue([])
    assert await run_instance_search(_radarr(), MASTER_KEY) == 1
    assert _commands(command_route) == [{"name": "MoviesSearch", "movieIds": [201]}]
    assert await _cooldown_rows() == [(2, 201, "movie")]


@pytest.mark.asyncio()
@respx.mock
async def test_queued_skip_does_not_wait_between_searches(seeded_instances: None) -> None:
    """The inter-search delay only follows a real dispatch."""
    _mock_radarr_missing([_movie(201)])
    _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201}])

    with patch("houndarr.engine.search_loop.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await run_instance_search(_radarr(), MASTER_KEY)

    sleep.assert_not_awaited()


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_skips_queued_item(seeded_instances: None) -> None:
    """The early retry after a release-timing block also honours the queue."""
    await seed_release_timing_retry(instance_id=1, item_id=101, item_type="episode")
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": 101}])

    assert await run_instance_search(_sonarr(), MASTER_KEY) == 0

    assert command_route.call_count == 0
    rows = await get_log_rows()
    assert [(r["action"], r["reason"]) for r in rows] == [
        ("skipped", "not yet released"),
        ("skipped", _QUEUED_REASON),
    ]


# ---------------------------------------------------------------------------
# Cutoff and upgrade passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_skips_queued_item(seeded_instances: None) -> None:
    _mock_radarr_missing([])
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        side_effect=_wanted_pages([_movie(301), _movie(302)]),
    )
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 301}])

    instance = _radarr(cutoff_enabled=True, cutoff_batch_size=1, cutoff_hourly_cap=5)
    assert await run_instance_search(instance, MASTER_KEY) == 1

    assert _commands(command_route) == [{"name": "MoviesSearch", "movieIds": [302]}]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["search_kind"], r["reason"]) for r in rows] == [
        ("skipped", 301, "cutoff", _QUEUED_REASON),
        ("searched", 302, "cutoff", None),
    ]


@pytest.mark.asyncio()
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_upgrade_pass_skips_queued_item(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    _mock_radarr_missing([])
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[_library_movie(401), _library_movie(402)]),
    )
    command_route = _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 401}])

    instance = _radarr(batch_size=0, upgrade_enabled=True, upgrade_batch_size=1)
    assert await run_instance_search(instance, MASTER_KEY) == 1

    assert _commands(command_route) == [{"name": "MoviesSearch", "movieIds": [402]}]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["search_kind"], r["reason"]) for r in rows] == [
        ("skipped", 401, "upgrade", _QUEUED_REASON),
        ("searched", 402, "upgrade", None),
    ]


# ---------------------------------------------------------------------------
# Season / artist context modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_season_context_sibling_still_drives_the_season_search(
    seeded_instances: None,
) -> None:
    """One queued episode does not hold back the season search for the others."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages([_episode(101, number=1), _episode(102, number=2)]),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": 101, "seriesId": 55, "seasonNumber": 1}])

    instance = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)
    assert await run_instance_search(instance, MASTER_KEY) == 1

    assert _commands(command_route) == [
        {"name": "SeasonSearch", "seriesId": 55, "seasonNumber": 1},
    ]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"]) for r in rows] == [
        ("searched", _season_item_id(55, 1)),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_season_context_skips_a_season_whose_episodes_are_all_queued(
    seeded_instances: None,
) -> None:
    """A fully queued season logs one skip row and the next season is still searched."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=_wanted_pages(
            [
                _episode(101, season=1, number=1),
                _episode(102, season=1, number=2),
                _episode(201, season=2, number=1),
            ]
        ),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": 101}, {"episodeId": 102}])

    instance = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)
    assert await run_instance_search(instance, MASTER_KEY) == 1

    assert _commands(command_route) == [
        {"name": "SeasonSearch", "seriesId": 55, "seasonNumber": 2},
    ]
    rows = await get_log_rows()
    assert [(r["action"], r["item_id"], r["reason"]) for r in rows] == [
        ("searched", _season_item_id(55, 2), None),
        ("skipped", _season_item_id(55, 1), _QUEUED_REASON),
    ]
    assert rows[1]["item_label"] == "My Show - S01 (season-context)"


@pytest.mark.parametrize(
    ("queued_albums", "expected_commands", "expected_rows"),
    [
        (
            [301],
            [{"name": "ArtistSearch", "artistId": 50}],
            [("searched", None)],
        ),
        ([301, 302], [], [("skipped", _QUEUED_REASON)]),
    ],
)
@pytest.mark.asyncio()
@respx.mock
async def test_artist_context_skips_only_when_every_seen_album_is_queued(
    seeded_instances: None,
    queued_albums: list[int],
    expected_commands: list[dict[str, Any]],
    expected_rows: list[tuple[str, str | None]],
) -> None:
    albums = [
        {**_ALBUM_RECORD, "id": 301, "title": "First"},
        {**_ALBUM_RECORD, "id": 302, "title": "Second"},
    ]
    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(side_effect=_wanted_pages(albums))
    command_route = _mock_command(LIDARR_URL, "v1")
    serve_download_queue([{"albumId": album_id} for album_id in queued_albums])

    instance = make_instance(
        instance_id=3,
        itype=InstanceType.lidarr,
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    await run_instance_search(instance, MASTER_KEY)

    assert _commands(command_route) == expected_commands
    rows = await get_log_rows()
    assert [(r["action"], r["reason"]) for r in rows] == expected_rows
    assert {r["item_id"] for r in rows} == {_artist_item_id(50)}


@pytest.mark.parametrize(
    ("queued_episodes", "expected_commands", "expected_rows"),
    [
        (
            [1101],
            [{"name": "SeasonSearch", "seriesId": 55, "seasonNumber": 1}],
            [("searched", None)],
        ),
        ([1101, 1102], [], [("skipped", _QUEUED_REASON)]),
    ],
)
@pytest.mark.asyncio()
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_upgrade_season_context_skips_only_fully_queued_seasons(
    mock_update: AsyncMock,
    seeded_instances: None,
    queued_episodes: list[int],
    expected_commands: list[dict[str, Any]],
    expected_rows: list[tuple[str, str | None]],
) -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{SONARR_URL}/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 55, "title": "My Show", "monitored": True}]),
    )
    respx.get(f"{SONARR_URL}/api/v3/episode").mock(
        return_value=httpx.Response(
            200,
            json=[_library_episode(1101, number=1), _library_episode(1102, number=2)],
        ),
    )
    command_route = _mock_command(SONARR_URL)
    serve_download_queue([{"episodeId": episode_id} for episode_id in queued_episodes])

    instance = _sonarr(
        batch_size=0,
        upgrade_enabled=True,
        upgrade_sonarr_search_mode=SonarrSearchMode.season_context,
    )
    await run_instance_search(instance, MASTER_KEY)

    assert _commands(command_route) == expected_commands
    rows = await get_log_rows()
    assert [(r["action"], r["reason"]) for r in rows] == expected_rows
    assert {(r["item_id"], r["search_kind"]) for r in rows} == {(_season_item_id(55, 1), "upgrade")}


# ---------------------------------------------------------------------------
# One lazy fetch per cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_queue_is_not_fetched_when_nothing_is_dispatched(
    seeded_instances: None,
) -> None:
    await record_search(2, 201, "movie")
    _mock_radarr_missing([_movie(201)])
    command_route = _mock_command(RADARR_URL)

    assert await run_instance_search(_radarr(), MASTER_KEY) == 0

    assert _queue_route().call_count == 0
    assert command_route.call_count == 0


@pytest.mark.asyncio()
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_queue_is_fetched_once_for_all_three_passes(
    mock_update: AsyncMock,
    seeded_instances: None,
) -> None:
    _mock_radarr_missing([_movie(201), _movie(202)])
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(side_effect=_wanted_pages([_movie(301)]))
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[_library_movie(401)]),
    )
    command_route = _mock_command(RADARR_URL)

    instance = _radarr(
        batch_size=2,
        cutoff_enabled=True,
        cutoff_hourly_cap=5,
        upgrade_enabled=True,
    )
    assert await run_instance_search(instance, MASTER_KEY) == 4

    assert command_route.call_count == 4
    assert _queue_route().call_count == 1


# ---------------------------------------------------------------------------
# Fail open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "queue_response",
    [
        httpx.Response(500, text="Internal Server Error"),
        httpx.Response(404),
        httpx.Response(200, text="<html>Sign in</html>"),
        httpx.Response(200, json={"records": []}),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
    ],
)
@pytest.mark.asyncio()
@respx.mock
async def test_failed_queue_fetch_searches_as_before(
    seeded_instances: None,
    caplog: pytest.LogCaptureFixture,
    queue_response: httpx.Response | Exception,
) -> None:
    """Any queue failure leaves searching exactly as it was, with one warning."""
    _mock_radarr_missing([_movie(201), _movie(202)])
    command_route = _mock_command(RADARR_URL)
    if isinstance(queue_response, Exception):
        _queue_route().mock(side_effect=queue_response)
    else:
        _queue_route().mock(return_value=queue_response)

    with caplog.at_level(logging.WARNING, logger="houndarr.engine.search_loop"):
        assert await run_instance_search(_radarr(), MASTER_KEY) == 2

    assert command_route.call_count == 2
    assert _queue_route().call_count == 1
    rows = await get_log_rows()
    assert [r["action"] for r in rows] == ["searched", "searched"]
    warnings = [r for r in caplog.records if "download queue check skipped" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio()
async def test_lookup_fails_open_when_the_client_cannot_be_built(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed URL raised at client construction is treated like any fetch failure."""
    adapter = MagicMock()
    adapter.make_client.side_effect = httpx.InvalidURL("bad url")
    lookup = _download_queue_lookup(adapter, _radarr())
    candidate = SearchCandidate(
        item_id=201,
        item_type="movie",
        label="Movie",
        unreleased_reason=None,
        group_key=None,
        search_payload={},
    )
    with caplog.at_level(logging.WARNING, logger="houndarr.engine.search_loop"):
        assert await lookup(candidate) is False
        assert await lookup(candidate) is False
    assert adapter.make_client.call_count == 1
    assert "download queue check skipped" in caplog.text


@pytest.mark.asyncio()
async def test_lookup_matches_context_candidates_on_leaf_id() -> None:
    """Season/artist/author candidates are matched on the wanted record, not the synthetic id."""

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get_queue_item_ids(self) -> frozenset[int]:
            return frozenset({101})

    adapter = MagicMock()
    adapter.make_client.return_value = _Client()
    lookup = _download_queue_lookup(adapter, _sonarr())

    def season_candidate(leaf_id: int | None) -> SearchCandidate:
        return SearchCandidate(
            item_id=_season_item_id(55, 1),
            item_type="episode",
            label="My Show - S01 (season-context)",
            unreleased_reason=None,
            group_key=(55, 1),
            search_payload={},
            leaf_id=leaf_id,
        )

    assert await lookup(season_candidate(101)) is True
    assert await lookup(season_candidate(102)) is False
    assert await lookup(season_candidate(None)) is False


# ---------------------------------------------------------------------------
# Skip-row throttling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_scheduled_cycles_log_a_queued_item_once_per_day(
    seeded_instances: None,
) -> None:
    _mock_radarr_missing([_movie(201)])
    _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201}])

    await run_instance_search(_radarr(), MASTER_KEY)
    await run_instance_search(_radarr(), MASTER_KEY)

    rows = await get_log_rows()
    assert [(r["action"], r["reason"]) for r in rows] == [("skipped", _QUEUED_REASON)]


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_always_logs_the_queued_skip(seeded_instances: None) -> None:
    _mock_radarr_missing([_movie(201)])
    _mock_command(RADARR_URL)
    serve_download_queue([{"movieId": 201}])

    await run_instance_search(_radarr(), MASTER_KEY, cycle_trigger="run_now")
    await run_instance_search(_radarr(), MASTER_KEY, cycle_trigger="run_now")

    rows = await get_log_rows()
    assert [(r["action"], r["reason"], r["cycle_trigger"]) for r in rows] == [
        ("skipped", _QUEUED_REASON, "run_now"),
        ("skipped", _QUEUED_REASON, "run_now"),
    ]

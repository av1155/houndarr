"""Tests for correct command payloads per app type and context-mode dedup."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.cooldown import record_search
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrSearchMode,
)

from .conftest import (
    _ALBUM_RECORD,
    _BOOK_RECORD,
    _COMMAND_RESP,
    _EPISODE_RECORD,
    _MOVIE_RECORD,
    _WHISPARR_EPISODE_RECORD,
    LIDARR_URL,
    MASTER_KEY,
    RADARR_URL,
    READARR_URL,
    SONARR_URL,
    WHISPARR_URL,
    get_log_rows,
    make_instance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_PAGE: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 0,
    "records": [],
}


def _page(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "page": 1,
        "pageSize": 50,
        "totalRecords": len(records),
        "records": records,
    }


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _radarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 2,
        "itype": InstanceType.radarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _lidarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 3,
        "itype": InstanceType.lidarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _readarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 4,
        "itype": InstanceType.readarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _whisparr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 5,
        "itype": InstanceType.whisparr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _get_post_body(call_index: int = 0) -> dict[str, Any]:
    """Extract the JSON body from the Nth POST call in respx."""
    content = respx.calls[call_index].request.content
    return json.loads(content)


def _find_post_bodies() -> list[dict[str, Any]]:
    """Collect JSON bodies from all POST calls."""
    bodies = []
    for call in respx.calls:
        if call.request.method == "POST":
            bodies.append(json.loads(call.request.content))
    return bodies


# ---------------------------------------------------------------------------
# Sonarr dispatch payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_episode_mode_payload(
    seeded_instances: None,
) -> None:
    """EpisodeSearch dispatches with episodeIds array."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_EPISODE_RECORD])),
    )
    cmd_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        sonarr_search_mode=SonarrSearchMode.episode,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [101]


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_season_context_payload(
    seeded_instances: None,
) -> None:
    """SeasonSearch dispatches with seriesId and seasonNumber."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_EPISODE_RECORD])),
    )
    cmd_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "SeasonSearch"
    assert body["seriesId"] == 55
    assert body["seasonNumber"] == 1


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_season_context_dedup(
    seeded_instances: None,
) -> None:
    """Two episodes from the same series+season: one SeasonSearch."""
    ep1 = {**_EPISODE_RECORD, "id": 101}
    ep2 = {**_EPISODE_RECORD, "id": 102, "episodeNumber": 2}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep1, ep2])),
    )
    cmd_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    assert cmd_route.call_count == 1


# ---------------------------------------------------------------------------
# Radarr dispatch payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_movies_search_payload(
    seeded_instances: None,
) -> None:
    """MoviesSearch dispatches with movieIds array."""
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_MOVIE_RECORD])),
    )
    cmd_route = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "MoviesSearch"
    assert body["movieIds"] == [201]


# ---------------------------------------------------------------------------
# Lidarr dispatch payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_lidarr_album_mode_payload(
    seeded_instances: None,
) -> None:
    """AlbumSearch dispatches with albumIds array."""
    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_ALBUM_RECORD])),
    )
    cmd_route = respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _lidarr_instance(
        lidarr_search_mode=LidarrSearchMode.album,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "AlbumSearch"
    assert body["albumIds"] == [301]


@pytest.mark.asyncio()
@respx.mock
async def test_lidarr_artist_context_payload(
    seeded_instances: None,
) -> None:
    """ArtistSearch dispatches with artistId."""
    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_ALBUM_RECORD])),
    )
    cmd_route = respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _lidarr_instance(
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "ArtistSearch"
    assert body["artistId"] == 50


@pytest.mark.asyncio()
@respx.mock
async def test_lidarr_artist_context_dedup(
    seeded_instances: None,
) -> None:
    """Two albums from the same artist: one ArtistSearch."""
    album1 = {**_ALBUM_RECORD, "id": 301}
    album2 = {**_ALBUM_RECORD, "id": 302, "title": "Second Album"}
    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([album1, album2])),
    )
    cmd_route = respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _lidarr_instance(
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    assert cmd_route.call_count == 1


# ---------------------------------------------------------------------------
# Readarr dispatch payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_readarr_book_mode_payload(
    seeded_instances: None,
) -> None:
    """BookSearch dispatches with bookIds array."""
    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_BOOK_RECORD])),
    )
    cmd_route = respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _readarr_instance(
        readarr_search_mode=ReadarrSearchMode.book,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "BookSearch"
    assert body["bookIds"] == [401]


@pytest.mark.asyncio()
@respx.mock
async def test_readarr_author_context_payload(
    seeded_instances: None,
) -> None:
    """AuthorSearch dispatches with authorId."""
    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_BOOK_RECORD])),
    )
    cmd_route = respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _readarr_instance(
        readarr_search_mode=ReadarrSearchMode.author_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "AuthorSearch"
    assert body["authorId"] == 60


@pytest.mark.asyncio()
@respx.mock
async def test_readarr_author_context_dedup(
    seeded_instances: None,
) -> None:
    """Two books from the same author: one AuthorSearch."""
    book1 = {**_BOOK_RECORD, "id": 401}
    book2 = {**_BOOK_RECORD, "id": 402, "title": "Second Book"}
    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([book1, book2])),
    )
    cmd_route = respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _readarr_instance(
        readarr_search_mode=ReadarrSearchMode.author_context,
    )
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    assert cmd_route.call_count == 1


# ---------------------------------------------------------------------------
# Whisparr dispatch payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_episode_mode_payload(
    seeded_instances: None,
) -> None:
    """EpisodeSearch dispatches with episodeIds for Whisparr."""
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_page([_WHISPARR_EPISODE_RECORD]),
        ),
    )
    cmd_route = respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _whisparr_instance(
        whisparr_search_mode=WhisparrSearchMode.episode,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [501]


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_season_context_payload(
    seeded_instances: None,
) -> None:
    """SeasonSearch dispatches with seriesId and seasonNumber for Whisparr."""
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_page([_WHISPARR_EPISODE_RECORD]),
        ),
    )
    cmd_route = respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _whisparr_instance(
        whisparr_search_mode=WhisparrSearchMode.season_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    assert cmd_route.call_count == 1
    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "SeasonSearch"
    assert body["seriesId"] == 70
    assert body["seasonNumber"] == 1


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_item_type_is_whisparr_episode(
    seeded_instances: None,
) -> None:
    """Whisparr log rows record item_type='whisparr_episode'."""
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_page([_WHISPARR_EPISODE_RECORD]),
        ),
    )
    respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _whisparr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched"]
    assert len(searched) == 1
    assert searched[0]["item_type"] == "whisparr_episode"


# ---------------------------------------------------------------------------
# Multiple items and error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_multiple_items_all_dispatched(
    seeded_instances: None,
) -> None:
    """3 items all searched: search POST called 3 times."""
    movies = [{**_MOVIE_RECORD, "id": 201 + i} for i in range(3)]
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page(movies)),
    )
    cmd_route = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance()
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 3
    assert cmd_route.call_count == 3


@pytest.mark.asyncio()
@respx.mock
async def test_skipped_item_no_search_post(
    seeded_instances: None,
) -> None:
    """Item on cooldown: POST not called for that item."""
    await record_search(2, 201, "movie")

    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_MOVIE_RECORD])),
    )
    cmd_route = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _radarr_instance(cooldown_days=7)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    assert cmd_route.call_count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_errored_dispatch_continues(
    seeded_instances: None,
) -> None:
    """Item 1 dispatch errors, item 2 still dispatched."""
    movies = [
        {**_MOVIE_RECORD, "id": 201},
        {**_MOVIE_RECORD, "id": 202},
    ]
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page(movies)),
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        side_effect=[
            httpx.Response(500, text="error"),
            httpx.Response(201, json=_COMMAND_RESP),
        ],
    )

    inst = _radarr_instance()
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched"]
    errors = [r for r in rows if r["action"] == "error"]
    assert len(searched) == 1
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Cutoff pass always uses item-level search mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_cutoff_uses_episode_search(
    seeded_instances: None,
) -> None:
    """Cutoff pass always uses EpisodeSearch even in season_context mode."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_page([_EPISODE_RECORD])),
    )
    cmd_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        cutoff_enabled=True,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [101]


@pytest.mark.asyncio()
@respx.mock
async def test_lidarr_cutoff_uses_album_search(
    seeded_instances: None,
) -> None:
    """Cutoff pass always uses AlbumSearch even in artist_context mode."""
    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{LIDARR_URL}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_page([_ALBUM_RECORD])),
    )
    cmd_route = respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _lidarr_instance(
        cutoff_enabled=True,
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "AlbumSearch"
    assert body["albumIds"] == [301]


@pytest.mark.asyncio()
@respx.mock
async def test_readarr_cutoff_uses_book_search(
    seeded_instances: None,
) -> None:
    """Cutoff pass always uses BookSearch even in author_context mode."""
    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{READARR_URL}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_page([_BOOK_RECORD])),
    )
    cmd_route = respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _readarr_instance(
        cutoff_enabled=True,
        readarr_search_mode=ReadarrSearchMode.author_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "BookSearch"
    assert body["bookIds"] == [401]


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_cutoff_uses_episode_search(
    seeded_instances: None,
) -> None:
    """Cutoff pass always uses EpisodeSearch even in season_context."""
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{WHISPARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(
            200,
            json=_page([_WHISPARR_EPISODE_RECORD]),
        ),
    )
    cmd_route = respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _whisparr_instance(
        cutoff_enabled=True,
        whisparr_search_mode=WhisparrSearchMode.season_context,
    )
    await run_instance_search(inst, MASTER_KEY)

    body = json.loads(cmd_route.calls[0].request.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [501]

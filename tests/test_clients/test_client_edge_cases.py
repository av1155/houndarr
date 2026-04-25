"""Tests for client edge cases: queue status, cutoff_unmet, search, error propagation."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrClient

# ---------------------------------------------------------------------------
# Queue status: per-app path verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_queue_status_success() -> None:
    """SonarrClient.get_queue_status returns the parsed JSON dict."""
    payload = {"totalCount": 5, "unknownCount": 0}
    respx.get("http://sonarr:8989/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with SonarrClient(url="http://sonarr:8989", api_key="test") as client:
        result = await client.get_queue_status()
    assert result["totalCount"] == 5


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_queue_status_path() -> None:
    """SonarrClient requests /api/v3/queue/status."""
    route = respx.get("http://sonarr:8989/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json={"totalCount": 0}),
    )
    async with SonarrClient(url="http://sonarr:8989", api_key="test") as client:
        await client.get_queue_status()
    assert route.called


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_queue_status_success() -> None:
    """RadarrClient.get_queue_status returns the parsed JSON dict."""
    payload = {"totalCount": 3}
    respx.get("http://radarr:7878/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with RadarrClient(url="http://radarr:7878", api_key="test") as client:
        result = await client.get_queue_status()
    assert result["totalCount"] == 3


@pytest.mark.asyncio()
@respx.mock
async def test_lidarr_queue_status_uses_v1_path() -> None:
    """LidarrClient uses /api/v1/queue/status (not v3)."""
    route = respx.get("http://lidarr:8686/api/v1/queue/status").mock(
        return_value=httpx.Response(200, json={"totalCount": 2}),
    )
    async with LidarrClient(url="http://lidarr:8686", api_key="test") as client:
        result = await client.get_queue_status()
    assert route.called
    assert result["totalCount"] == 2


@pytest.mark.asyncio()
@respx.mock
async def test_readarr_queue_status_uses_v1_path() -> None:
    """ReadarrClient uses /api/v1/queue/status (not v3)."""
    route = respx.get("http://readarr:8787/api/v1/queue/status").mock(
        return_value=httpx.Response(200, json={"totalCount": 1}),
    )
    async with ReadarrClient(url="http://readarr:8787", api_key="test") as client:
        result = await client.get_queue_status()
    assert route.called
    assert result["totalCount"] == 1


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_queue_status_uses_v3_path() -> None:
    """WhisparrClient uses /api/v3/queue/status."""
    route = respx.get("http://whisparr:6969/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json={"totalCount": 0}),
    )
    async with WhisparrClient(url="http://whisparr:6969", api_key="test") as client:
        result = await client.get_queue_status()
    assert route.called
    assert result["totalCount"] == 0


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_queue_status_http_500_raises() -> None:
    """HTTP 500 on queue/status raises HTTPStatusError."""
    respx.get("http://sonarr:8989/api/v3/queue/status").mock(
        return_value=httpx.Response(500),
    )
    async with SonarrClient(url="http://sonarr:8989", api_key="test") as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_queue_status()


@pytest.mark.asyncio()
@respx.mock
async def test_queue_status_transport_error_propagates() -> None:
    """A ConnectError on queue/status propagates as-is."""
    respx.get("http://sonarr:8989/api/v3/queue/status").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    async with SonarrClient(url="http://sonarr:8989", api_key="test") as client:
        with pytest.raises(httpx.ConnectError):
            await client.get_queue_status()


# ---------------------------------------------------------------------------
# get_cutoff_unmet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_get_cutoff_unmet_returns_episodes() -> None:
    """SonarrClient.get_cutoff_unmet parses the paginated response."""
    payload = {
        "page": 1,
        "pageSize": 10,
        "totalRecords": 1,
        "records": [
            {
                "id": 101,
                "seriesId": 55,
                "title": "Pilot",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "airDateUtc": "2023-09-01T00:00:00Z",
                "series": {"title": "My Show"},
            },
        ],
    }
    respx.get("http://sonarr:8989/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with SonarrClient(url="http://sonarr:8989", api_key="test") as client:
        episodes = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(episodes) == 1
    assert episodes[0].episode_id == 101
    assert episodes[0].series_title == "My Show"


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_get_cutoff_unmet_returns_movies() -> None:
    """RadarrClient.get_cutoff_unmet parses the paginated response."""
    payload = {
        "page": 1,
        "pageSize": 10,
        "totalRecords": 1,
        "records": [
            {
                "id": 201,
                "title": "My Movie",
                "year": 2023,
                "status": "released",
                "minimumAvailability": "released",
                "isAvailable": True,
                "inCinemas": "2023-01-01T00:00:00Z",
                "physicalRelease": "2023-02-01T00:00:00Z",
                "releaseDate": "2023-02-01T00:00:00Z",
                "digitalRelease": None,
            },
        ],
    }
    respx.get("http://radarr:7878/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with RadarrClient(url="http://radarr:7878", api_key="test") as client:
        movies = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(movies) == 1
    assert movies[0].movie_id == 201
    assert movies[0].title == "My Movie"


# ---------------------------------------------------------------------------
# search: verify POST body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_sonarr_search_posts_episode_search() -> None:
    """SonarrClient.search POSTs EpisodeSearch with the correct episode ID."""
    route = respx.post("http://sonarr:8989/api/v3/command").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "EpisodeSearch"}),
    )
    async with SonarrClient(url="http://sonarr:8989", api_key="test") as client:
        await client.search(101)

    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [101]


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_search_posts_movies_search() -> None:
    """RadarrClient.search POSTs MoviesSearch with the correct movie ID."""
    route = respx.post("http://radarr:7878/api/v3/command").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "MoviesSearch"}),
    )
    async with RadarrClient(url="http://radarr:7878", api_key="test") as client:
        await client.search(201)

    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "MoviesSearch"
    assert body["movieIds"] == [201]


# ---------------------------------------------------------------------------
# get_library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_get_library_returns_movies() -> None:
    """RadarrClient.get_library parses the flat array response."""
    payload = [
        {
            "id": 201,
            "title": "My Movie",
            "year": 2023,
            "monitored": True,
            "hasFile": True,
            "movieFile": {"qualityCutoffNotMet": False},
            "inCinemas": "2023-01-01T00:00:00Z",
            "physicalRelease": None,
            "digitalRelease": None,
        },
    ]
    respx.get("http://radarr:7878/api/v3/movie").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with RadarrClient(url="http://radarr:7878", api_key="test") as client:
        movies = await client.get_library()
    assert len(movies) == 1
    assert movies[0].movie_id == 201
    assert movies[0].monitored is True
    assert movies[0].has_file is True


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_library_cutoff_met_parsing() -> None:
    """qualityCutoffNotMet=False means cutoff_met=True on LibraryMovie."""
    payload = [
        {
            "id": 202,
            "title": "Quality Movie",
            "year": 2022,
            "monitored": True,
            "hasFile": True,
            "movieFile": {"qualityCutoffNotMet": False},
            "inCinemas": None,
            "physicalRelease": None,
            "digitalRelease": None,
        },
    ]
    respx.get("http://radarr:7878/api/v3/movie").mock(
        return_value=httpx.Response(200, json=payload),
    )
    async with RadarrClient(url="http://radarr:7878", api_key="test") as client:
        movies = await client.get_library()
    assert movies[0].cutoff_met is True


# ---------------------------------------------------------------------------
# aclose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_client_aclose_callable() -> None:
    """aclose() can be called without error, closing the underlying client."""
    client = SonarrClient(url="http://sonarr:8989", api_key="test")
    await client.aclose()
    assert client._client.is_closed  # noqa: SLF001

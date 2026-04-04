"""Tests for WhisparrV3Client - all HTTP calls mocked with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from houndarr.clients.whisparr_v3 import (
    LibraryWhisparrV3Movie,
    MissingWhisparrV3Movie,
    WhisparrV3Client,
)

BASE = "http://whisparr-v3:6969"
API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> WhisparrV3Client:
    return WhisparrV3Client(url=BASE, api_key=API_KEY)


# ---------------------------------------------------------------------------
# Sample movie records (Radarr-style API)
# ---------------------------------------------------------------------------

_MOVIE_RECORD_MISSING = {
    "id": 101,
    "title": "Scene One",
    "year": 2024,
    "status": "released",
    "minimumAvailability": "released",
    "isAvailable": True,
    "inCinemas": "2024-06-15T00:00:00Z",
    "physicalRelease": None,
    "releaseDate": None,
    "digitalRelease": "2024-06-15",
    "hasFile": False,
    "monitored": True,
}

_MOVIE_RECORD_WITH_FILE = {
    "id": 102,
    "title": "Scene Two",
    "year": 2023,
    "status": "released",
    "minimumAvailability": "released",
    "isAvailable": True,
    "inCinemas": "2023-01-10T00:00:00Z",
    "physicalRelease": None,
    "releaseDate": None,
    "digitalRelease": "2023-01-10",
    "hasFile": True,
    "monitored": True,
    "movieFile": {"qualityCutoffNotMet": True},
}

_MOVIE_RECORD_UNMONITORED = {
    "id": 103,
    "title": "Scene Three",
    "year": 2024,
    "hasFile": False,
    "monitored": False,
}

_MOVIE_RECORD_CUTOFF_MET = {
    "id": 104,
    "title": "Scene Four",
    "year": 2024,
    "hasFile": True,
    "monitored": True,
    "movieFile": {"qualityCutoffNotMet": False},
}

_ALL_MOVIES = [
    _MOVIE_RECORD_MISSING,
    _MOVIE_RECORD_WITH_FILE,
    _MOVIE_RECORD_UNMONITORED,
    _MOVIE_RECORD_CUTOFF_MET,
]


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_ping_success(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"appName": "Whisparr", "version": "3.3.2.604"})
    )
    result = await client.ping()
    assert result is not None
    assert result["appName"] == "Whisparr"
    assert result["version"] == "3.3.2.604"


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_none(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is None


# ---------------------------------------------------------------------------
# get_missing - fetches /api/v3/movie and filters client-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_filters_monitored_no_file(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(200, json=_ALL_MOVIES))
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    movie = results[0]
    assert isinstance(movie, MissingWhisparrV3Movie)
    assert movie.movie_id == 101
    assert movie.title == "Scene One"
    assert movie.year == 2024
    assert movie.digital_release == "2024-06-15"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty_library(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(200, json=[]))
    results = await client.get_missing()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_pagination(client: WhisparrV3Client) -> None:
    """Client-side pagination returns correct slices."""
    movies = [
        {
            **_MOVIE_RECORD_MISSING,
            "id": i,
            "title": f"Movie {i}",
            "inCinemas": f"2024-0{i}-01T00:00:00Z",
        }
        for i in range(1, 6)
    ]
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(200, json=movies))
    page1 = await client.get_missing(page=1, page_size=2)
    assert len(page1) == 2
    assert page1[0].movie_id == 1

    page2 = await client.get_missing(page=2, page_size=2)
    assert len(page2) == 2
    assert page2[0].movie_id == 3

    page3 = await client.get_missing(page=3, page_size=2)
    assert len(page3) == 1
    assert page3[0].movie_id == 5


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_caches_library(client: WhisparrV3Client) -> None:
    """Library should be fetched only once per client instance."""
    route = respx.get(f"{BASE}/api/v3/movie").mock(
        return_value=httpx.Response(200, json=_ALL_MOVIES)
    )
    await client.get_missing(page=1, page_size=10)
    await client.get_missing(page=2, page_size=10)
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_non_2xx_raises(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_missing()


# ---------------------------------------------------------------------------
# search - MoviesSearch (movieIds is an array)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_posts_correct_payload(client: WhisparrV3Client) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "MoviesSearch"})
    )
    await client.search(101)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "MoviesSearch"
    assert body["movieIds"] == [101]


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: WhisparrV3Client) -> None:
    respx.post(f"{BASE}/api/v3/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search(101)


# ---------------------------------------------------------------------------
# get_cutoff_unmet - filters for has_file=True + qualityCutoffNotMet=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_filters_correctly(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(200, json=_ALL_MOVIES))
    results = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(results) == 1
    assert results[0].movie_id == 102
    assert results[0].title == "Scene Two"


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_empty(client: WhisparrV3Client) -> None:
    all_met = [{**_MOVIE_RECORD_CUTOFF_MET}]
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(200, json=all_met))
    results = await client.get_cutoff_unmet()
    assert results == []


# ---------------------------------------------------------------------------
# get_library - returns all movies with file/cutoff metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_get_library_returns_all(client: WhisparrV3Client) -> None:
    respx.get(f"{BASE}/api/v3/movie").mock(return_value=httpx.Response(200, json=_ALL_MOVIES))
    results = await client.get_library()
    assert len(results) == 4
    assert all(isinstance(m, LibraryWhisparrV3Movie) for m in results)
    cutoff_met_movie = next(m for m in results if m.movie_id == 104)
    assert cutoff_met_movie.cutoff_met is True
    cutoff_unmet_movie = next(m for m in results if m.movie_id == 102)
    assert cutoff_unmet_movie.cutoff_met is False


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_context_manager() -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(200, json={}))
    async with WhisparrV3Client(url=BASE, api_key=API_KEY) as c:
        result = await c.ping()
    assert result is not None

"""Tests for RadarrClient - all HTTP calls mocked with respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from houndarr.clients.radarr import MissingMovie, RadarrClient

BASE = "http://radarr:7878"
API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> RadarrClient:
    return RadarrClient(url=BASE, api_key=API_KEY)


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_ping_success(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"version": "5.0.0"})
    )
    assert await client.ping() is True


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_false(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is False


@pytest.mark.asyncio()
@respx.mock
async def test_ping_network_error_returns_false(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is False


# ---------------------------------------------------------------------------
# get_missing
# ---------------------------------------------------------------------------

_MOVIE_RECORD = {
    "id": 201,
    "title": "Great Film",
    "year": 2022,
    "status": "released",
    "minimumAvailability": "released",
    "isAvailable": True,
    "inCinemas": "2022-10-15T00:00:00Z",
    "physicalRelease": "2022-12-05T00:00:00Z",
    "releaseDate": "2022-12-01T00:00:00Z",
    "digitalRelease": "2022-12-01",
}

_MISSING_RESPONSE = {"page": 1, "pageSize": 10, "totalRecords": 1, "records": [_MOVIE_RECORD]}


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_returns_movies(client: RadarrClient) -> None:
    route = respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RESPONSE)
    )
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    movie = results[0]
    assert isinstance(movie, MissingMovie)
    assert movie.movie_id == 201
    assert movie.title == "Great Film"
    assert movie.year == 2022
    assert movie.status == "released"
    assert movie.minimum_availability == "released"
    assert movie.is_available is True
    assert movie.in_cinemas == "2022-10-15T00:00:00Z"
    assert movie.physical_release == "2022-12-05T00:00:00Z"
    assert movie.release_date == "2022-12-01T00:00:00Z"
    assert movie.digital_release == "2022-12-01"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_missing()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_null_digital_release(client: RadarrClient) -> None:
    record = {**_MOVIE_RECORD, "digitalRelease": None}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].digital_release is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_parses_release_eligibility_fields(client: RadarrClient) -> None:
    record = {
        **_MOVIE_RECORD,
        "status": "announced",
        "minimumAvailability": "released",
        "isAvailable": False,
        "inCinemas": "2026-07-29T00:00:00Z",
        "physicalRelease": None,
        "releaseDate": "2026-10-27T00:00:00Z",
        "digitalRelease": None,
    }
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )

    result = (await client.get_missing())[0]
    assert result.status == "announced"
    assert result.minimum_availability == "released"
    assert result.is_available is False
    assert result.in_cinemas == "2026-07-29T00:00:00Z"
    assert result.physical_release is None
    assert result.release_date == "2026-10-27T00:00:00Z"
    assert result.digital_release is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_non_2xx_raises(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_missing()


# ---------------------------------------------------------------------------
# search / search_movie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_movie_posts_correct_payload(client: RadarrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "MoviesSearch"})
    )
    await client.search_movie(201)
    assert route.called
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["name"] == "MoviesSearch"
    assert body["movieIds"] == [201]


@pytest.mark.asyncio()
@respx.mock
async def test_search_alias_works(client: RadarrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )
    await client.search(202)
    assert route.called


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: RadarrClient) -> None:
    respx.post(f"{BASE}/api/v3/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_movie(201)


# ---------------------------------------------------------------------------
# get_cutoff_unmet
# ---------------------------------------------------------------------------

_CUTOFF_RESPONSE = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [
        {
            "id": 201,
            "title": "My Movie",
            "year": 2023,
            "digitalRelease": None,
        }
    ],
}


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_returns_movies(client: RadarrClient) -> None:
    route = respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_RESPONSE)
    )
    results = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(results) == 1
    movie = results[0]
    assert isinstance(movie, MissingMovie)
    assert movie.movie_id == 201
    assert movie.title == "My Movie"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_empty(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_cutoff_unmet()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_non_2xx_raises(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_cutoff_unmet()


# ---------------------------------------------------------------------------
# Timeout propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_timeout_propagates(client: RadarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(httpx.ReadTimeout):
        await client.get_missing()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_context_manager() -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(200, json={}))
    async with RadarrClient(url=BASE, api_key=API_KEY) as c:
        result = await c.ping()
    assert result is True

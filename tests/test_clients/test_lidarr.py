"""Tests for LidarrClient - all HTTP calls mocked with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from houndarr.clients.lidarr import LidarrClient, MissingAlbum

BASE = "http://lidarr:8686"
API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> LidarrClient:
    return LidarrClient(url=BASE, api_key=API_KEY)


# ---------------------------------------------------------------------------
# ping - uses /api/v1/system/status (NOT v3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_ping_success(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(
        return_value=httpx.Response(200, json={"version": "3.1.0"})
    )
    assert await client.ping() is True


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_false(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is False


@pytest.mark.asyncio()
@respx.mock
async def test_ping_network_error_returns_false(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is False


# ---------------------------------------------------------------------------
# get_missing - /api/v1/wanted/missing
# ---------------------------------------------------------------------------

_ALBUM_RECORD = {
    "id": 301,
    "artistId": 50,
    "title": "Greatest Hits",
    "releaseDate": "2023-03-15T00:00:00Z",
    "artist": {"id": 50, "artistName": "Test Artist"},
}

_MISSING_RESPONSE = {"page": 1, "pageSize": 10, "totalRecords": 1, "records": [_ALBUM_RECORD]}


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_returns_albums(client: LidarrClient) -> None:
    route = respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RESPONSE)
    )
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    album = results[0]
    assert isinstance(album, MissingAlbum)
    assert album.album_id == 301
    assert album.artist_id == 50
    assert album.artist_name == "Test Artist"
    assert album.title == "Greatest Hits"
    assert album.release_date == "2023-03-15T00:00:00Z"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"
    assert request.url.params["includeArtist"] == "true"
    assert request.url.params["sortKey"] == "releaseDate"
    assert request.url.params["sortDirection"] == "ascending"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_missing()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_null_release_date(client: LidarrClient) -> None:
    record = {**_ALBUM_RECORD, "releaseDate": None}
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_artist_id_fallback(client: LidarrClient) -> None:
    """When artistId is missing, falls back to artist.id."""
    record = {
        "id": 302,
        "title": "Fallback Album",
        "releaseDate": None,
        "artist": {"id": 77, "artistName": "Fallback Artist"},
    }
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].artist_id == 77
    assert results[0].artist_name == "Fallback Artist"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_no_artist_object(client: LidarrClient) -> None:
    """When artist key is entirely missing, defaults to 0 / empty string."""
    record = {"id": 303, "title": "Orphan Album", "releaseDate": None}
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].artist_id == 0
    assert results[0].artist_name == ""


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_non_2xx_raises(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_missing()


# ---------------------------------------------------------------------------
# search - AlbumSearch (albumIds is an array)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_posts_correct_payload(client: LidarrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "AlbumSearch"})
    )
    await client.search(301)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "AlbumSearch"
    assert body["albumIds"] == [301]


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: LidarrClient) -> None:
    respx.post(f"{BASE}/api/v1/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search(301)


# ---------------------------------------------------------------------------
# search_artist - ArtistSearch (artistId is a scalar)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_artist_posts_correct_payload(client: LidarrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 2, "name": "ArtistSearch"})
    )
    await client.search_artist(50)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "ArtistSearch"
    assert body["artistId"] == 50


@pytest.mark.asyncio()
@respx.mock
async def test_search_artist_non_2xx_raises(client: LidarrClient) -> None:
    respx.post(f"{BASE}/api/v1/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_artist(50)


# ---------------------------------------------------------------------------
# get_cutoff_unmet - /api/v1/wanted/cutoff
# ---------------------------------------------------------------------------

_CUTOFF_RESPONSE = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_ALBUM_RECORD],
}


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_returns_albums(client: LidarrClient) -> None:
    route = respx.get(f"{BASE}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_RESPONSE)
    )
    results = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(results) == 1
    album = results[0]
    assert isinstance(album, MissingAlbum)
    assert album.album_id == 301
    assert album.artist_name == "Test Artist"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"
    assert request.url.params["includeArtist"] == "true"


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_empty(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_cutoff_unmet()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_non_2xx_raises(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/cutoff").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_cutoff_unmet()


# ---------------------------------------------------------------------------
# Timeout propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_timeout_propagates(client: LidarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(httpx.ReadTimeout):
        await client.get_missing()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_context_manager() -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(return_value=httpx.Response(200, json={}))
    async with LidarrClient(url=BASE, api_key=API_KEY) as c:
        result = await c.ping()
    assert result is True

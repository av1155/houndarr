"""Tests for ReadarrClient — all HTTP calls mocked with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from houndarr.clients.readarr import MissingBook, ReadarrClient

BASE = "http://readarr:8787"
API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> ReadarrClient:
    return ReadarrClient(url=BASE, api_key=API_KEY)


# ---------------------------------------------------------------------------
# ping — uses /api/v1/system/status (NOT v3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_ping_success(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(
        return_value=httpx.Response(200, json={"version": "0.4.20"})
    )
    assert await client.ping() is True


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_false(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is False


@pytest.mark.asyncio()
@respx.mock
async def test_ping_network_error_returns_false(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/system/status").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is False


# ---------------------------------------------------------------------------
# get_missing — /api/v1/wanted/missing
# ---------------------------------------------------------------------------

_BOOK_RECORD = {
    "id": 401,
    "authorId": 60,
    "title": "Test Book",
    "releaseDate": "2023-06-01T00:00:00Z",
    "author": {"id": 60, "authorName": "Test Author"},
}

_MISSING_RESPONSE = {"page": 1, "pageSize": 10, "totalRecords": 1, "records": [_BOOK_RECORD]}


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_returns_books(client: ReadarrClient) -> None:
    route = respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RESPONSE)
    )
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    book = results[0]
    assert isinstance(book, MissingBook)
    assert book.book_id == 401
    assert book.author_id == 60
    assert book.author_name == "Test Author"
    assert book.title == "Test Book"
    assert book.release_date == "2023-06-01T00:00:00Z"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"
    assert request.url.params["includeAuthor"] == "true"
    assert request.url.params["sortKey"] == "releaseDate"
    assert request.url.params["sortDirection"] == "ascending"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_missing()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_null_release_date(client: ReadarrClient) -> None:
    record = {**_BOOK_RECORD, "releaseDate": None}
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_author_id_fallback(client: ReadarrClient) -> None:
    """When authorId is missing, falls back to author.id."""
    record = {
        "id": 402,
        "title": "Fallback Book",
        "releaseDate": None,
        "author": {"id": 88, "authorName": "Fallback Author"},
    }
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].author_id == 88
    assert results[0].author_name == "Fallback Author"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_no_author_object(client: ReadarrClient) -> None:
    """When author key is entirely missing, defaults to 0 / empty string."""
    record = {"id": 403, "title": "Orphan Book", "releaseDate": None}
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].author_id == 0
    assert results[0].author_name == ""


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_non_2xx_raises(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/missing").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_missing()


# ---------------------------------------------------------------------------
# search — BookSearch (bookIds is an array)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_posts_correct_payload(client: ReadarrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "BookSearch"})
    )
    await client.search(401)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "BookSearch"
    assert body["bookIds"] == [401]


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: ReadarrClient) -> None:
    respx.post(f"{BASE}/api/v1/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search(401)


# ---------------------------------------------------------------------------
# search_author — AuthorSearch (authorId is a scalar)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_author_posts_correct_payload(client: ReadarrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 2, "name": "AuthorSearch"})
    )
    await client.search_author(60)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "AuthorSearch"
    assert body["authorId"] == 60


@pytest.mark.asyncio()
@respx.mock
async def test_search_author_non_2xx_raises(client: ReadarrClient) -> None:
    respx.post(f"{BASE}/api/v1/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_author(60)


# ---------------------------------------------------------------------------
# get_cutoff_unmet — /api/v1/wanted/cutoff
# ---------------------------------------------------------------------------

_CUTOFF_RESPONSE = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_BOOK_RECORD],
}


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_returns_books(client: ReadarrClient) -> None:
    route = respx.get(f"{BASE}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_RESPONSE)
    )
    results = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(results) == 1
    book = results[0]
    assert isinstance(book, MissingBook)
    assert book.book_id == 401
    assert book.author_name == "Test Author"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"
    assert request.url.params["includeAuthor"] == "true"


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_empty(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_cutoff_unmet()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_non_2xx_raises(client: ReadarrClient) -> None:
    respx.get(f"{BASE}/api/v1/wanted/cutoff").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_cutoff_unmet()


# ---------------------------------------------------------------------------
# Timeout propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_timeout_propagates(client: ReadarrClient) -> None:
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
    async with ReadarrClient(url=BASE, api_key=API_KEY) as c:
        result = await c.ping()
    assert result is True

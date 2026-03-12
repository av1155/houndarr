"""Tests for SonarrClient — all HTTP calls mocked with respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from houndarr.clients.sonarr import MissingEpisode, SonarrClient

BASE = "http://sonarr:8989"
API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> SonarrClient:
    return SonarrClient(url=BASE, api_key=API_KEY)


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_ping_success(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"version": "4.0.0"})
    )
    assert await client.ping() is True


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_false(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is False


@pytest.mark.asyncio()
@respx.mock
async def test_ping_network_error_returns_false(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is False


# ---------------------------------------------------------------------------
# get_missing
# ---------------------------------------------------------------------------

_EPISODE_RECORD = {
    "id": 101,
    "title": "Pilot",
    "seasonNumber": 1,
    "episodeNumber": 1,
    "airDateUtc": "2023-09-01T00:00:00Z",
    "series": {"title": "My Show"},
}

_MISSING_RESPONSE = {"page": 1, "pageSize": 10, "totalRecords": 1, "records": [_EPISODE_RECORD]}


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_returns_episodes(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RESPONSE)
    )
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    ep = results[0]
    assert isinstance(ep, MissingEpisode)
    assert ep.episode_id == 101
    assert ep.series_title == "My Show"
    assert ep.episode_title == "Pilot"
    assert ep.season == 1
    assert ep.episode == 1
    assert ep.air_date_utc == "2023-09-01T00:00:00Z"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_missing()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_null_air_date(client: SonarrClient) -> None:
    record = {**_EPISODE_RECORD, "airDateUtc": None}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].air_date_utc is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_non_2xx_raises(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_missing()


# ---------------------------------------------------------------------------
# search / search_episode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_episode_posts_correct_payload(client: SonarrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "EpisodeSearch"})
    )
    await client.search_episode(101)
    assert route.called
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [101]


@pytest.mark.asyncio()
@respx.mock
async def test_search_alias_works(client: SonarrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )
    await client.search(202)
    assert route.called


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: SonarrClient) -> None:
    respx.post(f"{BASE}/api/v3/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_episode(101)


# ---------------------------------------------------------------------------
# Timeout propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_timeout_propagates(client: SonarrClient) -> None:
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
    async with SonarrClient(url=BASE, api_key=API_KEY) as c:
        result = await c.ping()
    assert result is True

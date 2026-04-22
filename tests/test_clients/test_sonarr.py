"""Tests for SonarrClient - all HTTP calls mocked with respx."""

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
        return_value=httpx.Response(200, json={"appName": "Sonarr", "version": "4.0.0"})
    )
    result = await client.ping()
    assert result is not None
    assert result.app_name == "Sonarr"


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_none(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is None


@pytest.mark.asyncio()
@respx.mock
async def test_ping_network_error_returns_none(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is None


# ---------------------------------------------------------------------------
# get_missing
# ---------------------------------------------------------------------------

_EPISODE_RECORD = {
    "id": 101,
    "seriesId": 55,
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
    route = respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RESPONSE)
    )
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    ep = results[0]
    assert isinstance(ep, MissingEpisode)
    assert ep.episode_id == 101
    assert ep.series_id == 55
    assert ep.series_title == "My Show"
    assert ep.episode_title == "Pilot"
    assert ep.season == 1
    assert ep.episode == 1
    assert ep.air_date_utc == "2023-09-01T00:00:00Z"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"


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
# search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_posts_correct_payload(client: SonarrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "EpisodeSearch"})
    )
    await client.search(101)
    assert route.called
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [101]


@pytest.mark.asyncio()
@respx.mock
async def test_search_season_posts_correct_payload(client: SonarrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 3, "name": "SeasonSearch"})
    )
    await client.search_season(55, 2)
    assert route.called
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["name"] == "SeasonSearch"
    assert body["seriesId"] == 55
    assert body["seasonNumber"] == 2


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: SonarrClient) -> None:
    respx.post(f"{BASE}/api/v3/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search(101)


# ---------------------------------------------------------------------------
# get_cutoff_unmet
# ---------------------------------------------------------------------------

_CUTOFF_RESPONSE = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_EPISODE_RECORD],
}


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_returns_episodes(client: SonarrClient) -> None:
    route = respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_RESPONSE)
    )
    results = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(results) == 1
    ep = results[0]
    assert isinstance(ep, MissingEpisode)
    assert ep.episode_id == 101
    assert ep.series_title == "My Show"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_empty(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_cutoff_unmet()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_non_2xx_raises(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_cutoff_unmet()


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
    assert result is not None


# ---------------------------------------------------------------------------
# get_wanted_total (#394)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_get_wanted_total_missing(client: SonarrClient) -> None:
    route = respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json={"page": 1, "pageSize": 1, "totalRecords": 437, "records": []},
        )
    )
    assert await client.get_wanted_total("missing") == 437
    assert route.calls[0].request.url.params["pageSize"] == "1"


@pytest.mark.asyncio()
@respx.mock
async def test_get_wanted_total_cutoff(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(
            200,
            json={"page": 1, "pageSize": 1, "totalRecords": 12, "records": []},
        )
    )
    assert await client.get_wanted_total("cutoff") == 12


@pytest.mark.asyncio()
@respx.mock
async def test_get_wanted_total_defaults_to_zero(client: SonarrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    assert await client.get_wanted_total("missing") == 0

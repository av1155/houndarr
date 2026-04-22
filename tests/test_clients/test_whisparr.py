"""Tests for WhisparrClient - all HTTP calls mocked with respx."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from houndarr.clients.whisparr_v2 import MissingWhisparrEpisode, WhisparrClient

BASE = "http://whisparr:6969"
API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> WhisparrClient:
    return WhisparrClient(url=BASE, api_key=API_KEY)


# ---------------------------------------------------------------------------
# ping - uses /api/v3/system/status (same as Sonarr)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_ping_success(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"appName": "Whisparr", "version": "2.2.0"})
    )
    result = await client.ping()
    assert result is not None
    assert result.app_name == "Whisparr"


@pytest.mark.asyncio()
@respx.mock
async def test_ping_non_2xx_returns_none(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(401))
    assert await client.ping() is None


@pytest.mark.asyncio()
@respx.mock
async def test_ping_network_error_returns_none(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/system/status").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is None


# ---------------------------------------------------------------------------
# get_missing - /api/v3/wanted/missing
# ---------------------------------------------------------------------------

_WHISPARR_EPISODE_RECORD = {
    "id": 501,
    "seriesId": 70,
    "title": "Scene Title",
    "seasonNumber": 1,
    "absoluteEpisodeNumber": 5,
    "releaseDate": "2023-09-01",
    "series": {"id": 70, "title": "My Whisparr Show"},
}

_MISSING_RESPONSE = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_WHISPARR_EPISODE_RECORD],
}


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_returns_episodes(client: WhisparrClient) -> None:
    route = respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RESPONSE)
    )
    results = await client.get_missing(page=1, page_size=10)
    assert len(results) == 1
    ep = results[0]
    assert isinstance(ep, MissingWhisparrEpisode)
    assert ep.episode_id == 501
    assert ep.series_id == 70
    assert ep.series_title == "My Whisparr Show"
    assert ep.episode_title == "Scene Title"
    assert ep.season_number == 1
    assert ep.absolute_episode_number == 5
    assert ep.release_date == datetime(2023, 9, 1, tzinfo=UTC)
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"
    assert request.url.params["includeSeries"] == "true"
    assert request.url.params["sortKey"] == "releaseDate"
    assert request.url.params["sortDirection"] == "ascending"


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_missing()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_null_release_date(client: WhisparrClient) -> None:
    """Null releaseDate results in release_date=None."""
    record = {**_WHISPARR_EPISODE_RECORD, "releaseDate": None}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_dict_release_date(client: WhisparrClient) -> None:
    """Legacy DateOnly dict format is still accepted."""
    record = {**_WHISPARR_EPISODE_RECORD, "releaseDate": {"year": 2023, "month": 9, "day": 1}}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date == datetime(2023, 9, 1, tzinfo=UTC)


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_empty_release_date_object(client: WhisparrClient) -> None:
    """Empty DateOnly object {} results in release_date=None."""
    record = {**_WHISPARR_EPISODE_RECORD, "releaseDate": {}}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_invalid_release_date_object(client: WhisparrClient) -> None:
    """Invalid DateOnly values (e.g., month=13) result in release_date=None."""
    record = {**_WHISPARR_EPISODE_RECORD, "releaseDate": {"year": 2023, "month": 13, "day": 1}}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_invalid_release_date_string(client: WhisparrClient) -> None:
    """Unparseable date strings result in release_date=None."""
    record = {**_WHISPARR_EPISODE_RECORD, "releaseDate": "not-a-date"}
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].release_date is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_series_title_fallback(client: WhisparrClient) -> None:
    """When series key is missing, falls back to seriesTitle field."""
    record = {
        "id": 502,
        "seriesId": 71,
        "title": "Another Scene",
        "seasonNumber": 2,
        "absoluteEpisodeNumber": None,
        "releaseDate": None,
        "seriesTitle": "Fallback Title",
    }
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].series_title == "Fallback Title"
    assert results[0].absolute_episode_number is None


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_series_id_fallback(client: WhisparrClient) -> None:
    """When seriesId is missing, falls back to series.id."""
    record = {
        "id": 503,
        "title": "No SeriesId",
        "seasonNumber": 1,
        "releaseDate": None,
        "series": {"id": 99, "title": "From Series"},
    }
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    results = await client.get_missing()
    assert results[0].series_id == 99


@pytest.mark.asyncio()
@respx.mock
async def test_get_missing_non_2xx_raises(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_missing()


# ---------------------------------------------------------------------------
# search - EpisodeSearch (episodeIds is an array)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_posts_correct_payload(client: WhisparrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "EpisodeSearch"})
    )
    await client.search(501)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "EpisodeSearch"
    assert body["episodeIds"] == [501]


@pytest.mark.asyncio()
@respx.mock
async def test_search_non_2xx_raises(client: WhisparrClient) -> None:
    respx.post(f"{BASE}/api/v3/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search(501)


# ---------------------------------------------------------------------------
# search_season - SeasonSearch (seriesId + seasonNumber are scalars)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_season_posts_correct_payload(client: WhisparrClient) -> None:
    route = respx.post(f"{BASE}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 3, "name": "SeasonSearch"})
    )
    await client.search_season(70, 2)
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "SeasonSearch"
    assert body["seriesId"] == 70
    assert body["seasonNumber"] == 2


@pytest.mark.asyncio()
@respx.mock
async def test_search_season_non_2xx_raises(client: WhisparrClient) -> None:
    respx.post(f"{BASE}/api/v3/command").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_season(70, 2)


# ---------------------------------------------------------------------------
# get_cutoff_unmet - /api/v3/wanted/cutoff
# ---------------------------------------------------------------------------

_CUTOFF_RESPONSE = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_WHISPARR_EPISODE_RECORD],
}


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_returns_episodes(client: WhisparrClient) -> None:
    route = respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_RESPONSE)
    )
    results = await client.get_cutoff_unmet(page=1, page_size=10)
    assert len(results) == 1
    ep = results[0]
    assert isinstance(ep, MissingWhisparrEpisode)
    assert ep.episode_id == 501
    assert ep.series_title == "My Whisparr Show"
    request = route.calls[0].request
    assert request.url.params["monitored"] == "true"
    assert request.url.params["includeSeries"] == "true"


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_empty(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )
    results = await client.get_cutoff_unmet()
    assert results == []


@pytest.mark.asyncio()
@respx.mock
async def test_get_cutoff_unmet_non_2xx_raises(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/cutoff").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_cutoff_unmet()


# ---------------------------------------------------------------------------
# Timeout propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_timeout_propagates(client: WhisparrClient) -> None:
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
    async with WhisparrClient(url=BASE, api_key=API_KEY) as c:
        result = await c.ping()
    assert result is not None


@pytest.mark.asyncio()
@respx.mock
async def test_get_wanted_total_missing(client: WhisparrClient) -> None:
    respx.get(f"{BASE}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"totalRecords": 33, "records": []})
    )
    assert await client.get_wanted_total("missing") == 33

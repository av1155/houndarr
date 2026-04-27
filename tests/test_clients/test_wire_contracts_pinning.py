"""Pin the exact query strings emitted by every *arr client's wanted probes.

Sonarr, Radarr, Lidarr, Readarr, and Whisparr v2 all route through
the shared ``_fetch_wanted_page`` template method on ``ArrClient``;
Whisparr v3 stays a documented outlier (no ``/wanted`` endpoint).

These tests capture the exact HTTP request each concrete client
issues today for ``get_missing``, ``get_cutoff_unmet``, and
``get_wanted_total`` so a future template edit cannot silently
drop
or reorder a query param or change the sort key.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client

pytestmark = pytest.mark.pinning


_EMPTY_PAGINATED = {"page": 1, "pageSize": 1, "totalRecords": 0, "records": []}


# Sonarr


class TestSonarrWireContract:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_missing_query(self) -> None:
        route = respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="k") as client:
            await client.get_missing(page=3, page_size=25)
        params = route.calls[0].request.url.params
        assert params["page"] == "3"
        assert params["pageSize"] == "25"
        assert params["sortKey"] == "airDateUtc"
        assert params["sortDirection"] == "ascending"
        assert params["includeSeries"] == "true"
        assert params["monitored"] == "true"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_cutoff_query(self) -> None:
        route = respx.get("http://sonarr:8989/api/v3/wanted/cutoff").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="k") as client:
            await client.get_cutoff_unmet(page=2, page_size=10)
        params = route.calls[0].request.url.params
        assert params["page"] == "2"
        assert params["pageSize"] == "10"
        assert params["includeSeries"] == "true"
        assert params["monitored"] == "true"
        assert "sortKey" not in params

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_wanted_total_probe(self) -> None:
        route = respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json={**_EMPTY_PAGINATED, "totalRecords": 99}),
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="k") as client:
            total = await client.get_wanted_total("missing")
        params = route.calls[0].request.url.params
        assert params["pageSize"] == "1"
        assert params["sortKey"] == "airDateUtc"
        assert params["monitored"] == "true"
        assert total == 99


# Radarr


class TestRadarrWireContract:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_missing_query(self) -> None:
        route = respx.get("http://radarr:7878/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with RadarrClient(url="http://radarr:7878", api_key="k") as client:
            await client.get_missing(page=1, page_size=10)
        params = route.calls[0].request.url.params
        assert params["sortKey"] == "inCinemas"
        assert params["sortDirection"] == "ascending"
        assert params["monitored"] == "true"
        assert "includeSeries" not in params  # Radarr has no series concept

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_cutoff_query(self) -> None:
        route = respx.get("http://radarr:7878/api/v3/wanted/cutoff").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with RadarrClient(url="http://radarr:7878", api_key="k") as client:
            await client.get_cutoff_unmet(page=1, page_size=10)
        params = route.calls[0].request.url.params
        assert params["monitored"] == "true"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_wanted_total_probe(self) -> None:
        route = respx.get("http://radarr:7878/api/v3/wanted/cutoff").mock(
            return_value=httpx.Response(200, json={**_EMPTY_PAGINATED, "totalRecords": 42}),
        )
        async with RadarrClient(url="http://radarr:7878", api_key="k") as client:
            total = await client.get_wanted_total("cutoff")
        params = route.calls[0].request.url.params
        assert params["pageSize"] == "1"
        assert total == 42


# Lidarr (v1 API)


class TestLidarrWireContract:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_missing_uses_v1_and_include_artist(self) -> None:
        route = respx.get("http://lidarr:8686/api/v1/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with LidarrClient(url="http://lidarr:8686", api_key="k") as client:
            await client.get_missing(page=1, page_size=10)
        params = route.calls[0].request.url.params
        assert params["includeArtist"] == "true"
        assert params["monitored"] == "true"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_cutoff_uses_v1(self) -> None:
        route = respx.get("http://lidarr:8686/api/v1/wanted/cutoff").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with LidarrClient(url="http://lidarr:8686", api_key="k") as client:
            await client.get_cutoff_unmet(page=1, page_size=10)
        assert route.called


# Readarr (v1 API)


class TestReadarrWireContract:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_missing_uses_v1_and_include_author(self) -> None:
        route = respx.get("http://readarr:8787/api/v1/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with ReadarrClient(url="http://readarr:8787", api_key="k") as client:
            await client.get_missing(page=1, page_size=10)
        params = route.calls[0].request.url.params
        assert params["includeAuthor"] == "true"
        assert params["monitored"] == "true"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_cutoff_uses_v1(self) -> None:
        route = respx.get("http://readarr:8787/api/v1/wanted/cutoff").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with ReadarrClient(url="http://readarr:8787", api_key="k") as client:
            await client.get_cutoff_unmet(page=1, page_size=10)
        assert route.called


# Whisparr v2 (Sonarr-shaped API at v3)


class TestWhisparrV2WireContract:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_missing_uses_v3_and_include_series(self) -> None:
        route = respx.get("http://whisparr:6969/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with WhisparrV2Client(url="http://whisparr:6969", api_key="k") as client:
            await client.get_missing(page=1, page_size=10)
        params = route.calls[0].request.url.params
        assert params["includeSeries"] == "true"
        assert params["monitored"] == "true"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_cutoff_uses_v3(self) -> None:
        route = respx.get("http://whisparr:6969/api/v3/wanted/cutoff").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with WhisparrV2Client(url="http://whisparr:6969", api_key="k") as client:
            await client.get_cutoff_unmet(page=1, page_size=10)
        assert route.called


# Cross-app path dispatch sanity: every wanted endpoint carries api key + accept


class TestCommonRequestHeaders:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_x_api_key_forwarded(self) -> None:
        route = respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="secret-k") as client:
            await client.get_missing(page=1, page_size=10)
        assert route.calls[0].request.headers.get("x-api-key") == "secret-k"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_accept_header_json(self) -> None:
        route = respx.get("http://sonarr:8989/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json=_EMPTY_PAGINATED),
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="k") as client:
            await client.get_missing(page=1, page_size=10)
        accept = route.calls[0].request.headers.get("accept")
        assert accept is not None
        assert "application/json" in accept

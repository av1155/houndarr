"""Pin ``get_wanted_page``: the parsed items plus what a walk needs to vouch for a list (#897)."""

from __future__ import annotations

import httpx
import pytest
import respx

from houndarr.clients.base import ArrClient, WantedKind
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from tests.mock_arr.server import create_app

_MOCK = "http://mock-arr"

_CLIENTS = [
    pytest.param(SonarrClient, "sonarr", "episode_id", id="sonarr"),
    pytest.param(RadarrClient, "radarr", "movie_id", id="radarr"),
    pytest.param(LidarrClient, "lidarr", "album_id", id="lidarr"),
    pytest.param(ReadarrClient, "readarr", "book_id", id="readarr"),
    pytest.param(WhisparrV2Client, "whisparr_v2", "episode_id", id="whisparr_v2"),
]


def _client(cls: type[ArrClient], app: str) -> ArrClient:
    return cls(url=f"{_MOCK}/{app}", api_key="k")


@pytest.mark.asyncio()
@pytest.mark.parametrize(("cls", "app", "id_field"), _CLIENTS)
@pytest.mark.parametrize("kind", ["missing", "cutoff"])
@respx.mock
async def test_a_page_carries_its_ids_and_the_list_total(
    cls: type[ArrClient], app: str, id_field: str, kind: WantedKind
) -> None:
    mock = create_app()
    respx.route(host="mock-arr").mock(side_effect=respx.ASGIHandler(mock))
    data = getattr(mock.state.mock_state, app)

    async with _client(cls, app) as client:
        page = await client.get_wanted_page(kind, page=2, page_size=7)
        list_page = client.get_missing if kind == "missing" else client.get_cutoff_unmet
        items = await list_page(page=2, page_size=7)

    assert page.ids == tuple(getattr(item, id_field) for item in page.items)
    assert len(page.ids) == 7
    assert page.items == items
    assert page.total_records == len(data.missing_ids if kind == "missing" else data.cutoff_ids)
    assert page.total_counts_rows is (app == "readarr" and kind == "cutoff")


@pytest.mark.asyncio()
@pytest.mark.parametrize(("cls", "app", "id_field"), _CLIENTS)
@respx.mock
async def test_a_page_without_a_total_reports_none(
    cls: type[ArrClient], app: str, id_field: str
) -> None:
    mock = create_app()

    async def strip_total(request: httpx.Request) -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mock)) as direct:
            envelope = (await direct.get(str(request.url))).json()
        return httpx.Response(200, json={"records": envelope["records"]})

    respx.route(host="mock-arr", path__regex=r"/wanted/").mock(side_effect=strip_total)
    respx.route(host="mock-arr").mock(side_effect=respx.ASGIHandler(mock))

    async with _client(cls, app) as client:
        page = await client.get_wanted_page("missing", page=1, page_size=3)

    assert (len(page.items), page.total_records) == (3, None)


@pytest.mark.asyncio()
@respx.mock
async def test_readarr_fills_null_authors_and_keeps_the_wire_ids() -> None:
    book = {"id": 401, "authorId": 60, "title": "T", "releaseDate": None, "author": None}
    respx.get("http://readarr:8787/api/v1/wanted/missing").respond(
        json={"page": 1, "pageSize": 10, "totalRecords": 2, "records": [book, {**book, "id": 402}]}
    )
    respx.get("http://readarr:8787/api/v1/author").respond(
        json=[{"id": 60, "authorName": "Hydrated Author"}]
    )

    async with ReadarrClient(url="http://readarr:8787", api_key="k") as client:
        page = await client.get_wanted_page("missing", page=1, page_size=10)

    assert page.ids == (401, 402)
    assert [b.author_name for b in page.items] == ["Hydrated Author", "Hydrated Author"]
    assert page.total_records == 2


@pytest.mark.asyncio()
async def test_a_client_without_wanted_lists_refuses() -> None:
    async with WhisparrV3Client(url="http://whisparr:6969", api_key="k") as client:
        with pytest.raises(NotImplementedError):
            await client.get_wanted_page("missing", page=1, page_size=10)

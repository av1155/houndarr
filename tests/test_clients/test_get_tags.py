"""``ArrClient.get_tags``: the per-cycle ``/tag`` lookup behind tag filters."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.errors import ClientHTTPError, ClientTransportError, ClientValidationError

_SONARR_URL = "http://sonarr:8989"
_SONARR_TAGS = f"{_SONARR_URL}/api/v3/tag"


@pytest.mark.asyncio()
@respx.mock
async def test_returns_lowercased_label_to_id_map() -> None:
    respx.get(_SONARR_TAGS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 1, "label": "1080P"}, {"id": 7, "label": "x"}]
        )
    )
    async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
        assert await client.get_tags() == {"1080p": 1, "x": 7}


@pytest.mark.asyncio()
@respx.mock
async def test_v1_apps_use_the_v1_path() -> None:
    route = respx.get("http://lidarr:8686/api/v1/tag").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with LidarrClient(url="http://lidarr:8686", api_key="k") as client:
        assert await client.get_tags() == {}
    assert route.called


@pytest.mark.asyncio()
@respx.mock
async def test_http_error_wraps_to_client_http_error() -> None:
    respx.get(_SONARR_TAGS).mock(return_value=httpx.Response(503))
    async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
        with pytest.raises(ClientHTTPError, match=r"^get_tags: HTTP 503 from /api/v3/tag$"):
            await client.get_tags()


@pytest.mark.asyncio()
@respx.mock
async def test_transport_error_wraps_to_client_transport_error() -> None:
    respx.get(_SONARR_TAGS).mock(side_effect=httpx.ConnectError("refused"))
    async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
        with pytest.raises(ClientTransportError):
            await client.get_tags()


@pytest.mark.asyncio()
@respx.mock
async def test_non_json_body_wraps_to_client_validation_error() -> None:
    """A proxy login page served with 200 must not escape as a raw ValueError."""
    respx.get(_SONARR_TAGS).mock(return_value=httpx.Response(200, text="<html>Sign in</html>"))
    async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
        with pytest.raises(ClientValidationError) as exc_info:
            await client.get_tags()
    assert str(exc_info.value) == "get_tags: non-JSON body from /api/v3/tag"
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)


@pytest.mark.parametrize("payload", [{"records": []}, [{"id": 1}], [{"id": 1, "label": None}]])
@pytest.mark.asyncio()
@respx.mock
async def test_unexpected_shape_wraps_to_client_validation_error(payload: object) -> None:
    respx.get(_SONARR_TAGS).mock(return_value=httpx.Response(200, json=payload))
    async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
        with pytest.raises(ClientValidationError):
            await client.get_tags()

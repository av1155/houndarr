"""Pin ``ArrClient.get_queue_item_ids``: wire contract, per-app request shape, typed errors.

The engine's per-item download-queue check (issue #765) reads this set once
per dispatching cycle and fails open on any :class:`ClientError`, so every
failure mode here must surface as one of the typed subclasses.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from houndarr.clients._wire_models import QueueRecord
from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from houndarr.errors import ClientHTTPError, ClientTransportError, ClientValidationError

pytestmark = pytest.mark.pinning

_SONARR_URL = "http://sonarr:8989"
_SONARR_DETAILS = f"{_SONARR_URL}/api/v3/queue/details"


class TestQueueRecordWireContract:
    """Pin how one ``/queue/details`` entry maps to a leaf id."""

    @pytest.mark.parametrize("key", ["episodeId", "movieId", "albumId", "bookId"])
    def test_item_id_reads_whichever_leaf_field_is_present(self, key: str) -> None:
        assert QueueRecord.model_validate({key: 42}).item_id == 42

    def test_unmatched_download_has_no_item_id(self) -> None:
        """Downloads the *arr could not match omit the id key entirely."""
        record = QueueRecord.model_validate({"title": "Unknown.Release", "seriesId": 9})
        assert record.item_id is None

    def test_null_leaf_id_has_no_item_id(self) -> None:
        assert QueueRecord.model_validate({"episodeId": None}).item_id is None

    def test_extra_fields_ignored(self) -> None:
        record = QueueRecord.model_validate(
            {
                "movieId": 7,
                "status": "delay",
                "quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}},
                "brand_new_future_field": True,
            }
        )
        assert record.item_id == 7

    def test_non_integer_leaf_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueueRecord.model_validate({"episodeId": "not-a-number"})


_V3_DETAILS = "/api/v3/queue/details"
_V1_DETAILS = "/api/v1/queue/details"

_APP_CASES: list[tuple[type[ArrClient], str, str, str, dict[str, str]]] = [
    (SonarrClient, "http://sonarr:8989", _V3_DETAILS, "episodeId", {}),
    (RadarrClient, "http://radarr:7878", _V3_DETAILS, "movieId", {}),
    (WhisparrV2Client, "http://whisparr:6969", _V3_DETAILS, "episodeId", {}),
    (WhisparrV3Client, "http://whisparr-v3:6970", _V3_DETAILS, "movieId", {}),
    (LidarrClient, "http://lidarr:8686", _V1_DETAILS, "albumId", {"includeAlbum": "false"}),
    (ReadarrClient, "http://readarr:8787", _V1_DETAILS, "bookId", {"includeBook": "false"}),
]


class TestQueueItemIdsRequestShape:
    """Pin the per-app path, query params, and id extraction."""

    @pytest.mark.parametrize(("client_cls", "url", "path", "leaf_key", "params"), _APP_CASES)
    @pytest.mark.asyncio()
    @respx.mock
    async def test_reads_leaf_ids_from_the_app_details_path(
        self,
        client_cls: type[ArrClient],
        url: str,
        path: str,
        leaf_key: str,
        params: dict[str, str],
    ) -> None:
        records: list[dict[str, Any]] = [
            {leaf_key: 11, "status": "downloading"},
            {leaf_key: 12, "status": "delay"},
            {leaf_key: 11, "status": "importBlocked"},
            {"title": "Unmatched.Release", "status": "warning"},
        ]
        route = respx.get(f"{url}{path}").mock(return_value=httpx.Response(200, json=records))

        async with client_cls(url=url, api_key="k") as client:
            ids = await client.get_queue_item_ids()

        assert ids == frozenset({11, 12})
        assert route.call_count == 1
        assert dict(route.calls.last.request.url.params) == params

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_queue_returns_empty_set(self) -> None:
        respx.get(_SONARR_DETAILS).mock(return_value=httpx.Response(200, json=[]))
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            assert await client.get_queue_item_ids() == frozenset()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sends_the_api_key(self) -> None:
        route = respx.get(_SONARR_DETAILS).mock(return_value=httpx.Response(200, json=[]))
        async with SonarrClient(url=_SONARR_URL, api_key="secret-key") as client:
            await client.get_queue_item_ids()
        assert route.calls.last.request.headers["X-Api-Key"] == "secret-key"


class TestQueueItemIdsTypedErrors:
    """Every failure surfaces as a :class:`ClientError` subclass the engine fails open on."""

    @pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
    @pytest.mark.asyncio()
    @respx.mock
    async def test_non_2xx_wraps_to_client_http_error(self, status: int) -> None:
        respx.get(_SONARR_DETAILS).mock(return_value=httpx.Response(status))
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            with pytest.raises(ClientHTTPError) as exc_info:
                await client.get_queue_item_ids()
        assert str(status) in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_redirect_wraps_to_client_http_error(self) -> None:
        """Redirects are never followed, so a 3xx is a failed fetch."""
        respx.get(_SONARR_DETAILS).mock(
            return_value=httpx.Response(302, headers={"Location": "/login"}),
        )
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            with pytest.raises(ClientHTTPError):
                await client.get_queue_item_ids()

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("connection refused"),
            httpx.ReadTimeout("read timed out"),
            httpx.InvalidURL("bad url"),
        ],
    )
    @pytest.mark.asyncio()
    @respx.mock
    async def test_transport_failures_wrap_to_client_transport_error(
        self, error: Exception
    ) -> None:
        respx.get(_SONARR_DETAILS).mock(side_effect=error)
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            with pytest.raises(ClientTransportError) as exc_info:
                await client.get_queue_item_ids()
        assert exc_info.value.__cause__ is error

    @pytest.mark.asyncio()
    @respx.mock
    async def test_non_json_body_wraps_to_client_validation_error(self) -> None:
        """A proxy login page served with 200 must not escape as a raw ValueError."""
        respx.get(_SONARR_DETAILS).mock(
            return_value=httpx.Response(200, text="<html>Sign in</html>"),
        )
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            with pytest.raises(ClientValidationError) as exc_info:
                await client.get_queue_item_ids()
        assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)

    @pytest.mark.parametrize("payload", [{"records": []}, "queue", 5, None])
    @pytest.mark.asyncio()
    @respx.mock
    async def test_non_list_payload_wraps_to_client_validation_error(self, payload: Any) -> None:
        respx.get(_SONARR_DETAILS).mock(return_value=httpx.Response(200, json=payload))
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            with pytest.raises(ClientValidationError):
                await client.get_queue_item_ids()

    @pytest.mark.parametrize("row", [7, "row", [1], {"episodeId": "abc"}])
    @pytest.mark.asyncio()
    @respx.mock
    async def test_malformed_row_wraps_to_client_validation_error(self, row: Any) -> None:
        respx.get(_SONARR_DETAILS).mock(
            return_value=httpx.Response(200, json=[{"episodeId": 1}, row]),
        )
        async with SonarrClient(url=_SONARR_URL, api_key="k") as client:
            with pytest.raises(ClientValidationError) as exc_info:
                await client.get_queue_item_ids()
        assert isinstance(exc_info.value.__cause__, ValidationError)

"""Pin the typed-error surface on every ``ArrClient.get_wanted_total`` override.

Every client wraps raw ``httpx`` and ``pydantic`` failures into the
typed :class:`~houndarr.errors.ClientError` hierarchy and preserves
the original exception on ``__cause__``.

The five paginated clients (Sonarr, Radarr, Lidarr, Readarr, Whisparr v2)
share a structurally identical wrap around a size-1 ``/wanted/{kind}``
probe; their tests are parametrized over the client / URL / endpoint
path tuple.  Whisparr v3 has its own tests because its total is
computed from ``/api/v3/movie`` rather than a ``/wanted`` probe.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from houndarr.errors import (
    ClientHTTPError,
    ClientTransportError,
    ClientValidationError,
)

pytestmark = pytest.mark.pinning


# Paginated clients: Sonarr / Radarr / Lidarr / Readarr / Whisparr v2

ClientFactory = Callable[..., ArrClient]

_PAGINATED_CASES: list[tuple[str, ClientFactory, str, str]] = [
    ("sonarr", SonarrClient, "http://sonarr:8989", "/api/v3/wanted/missing"),
    ("radarr", RadarrClient, "http://radarr:7878", "/api/v3/wanted/missing"),
    ("lidarr", LidarrClient, "http://lidarr:8686", "/api/v1/wanted/missing"),
    ("readarr", ReadarrClient, "http://readarr:8787", "/api/v1/wanted/missing"),
    ("whisparr_v2", WhisparrV2Client, "http://whisparr:6969", "/api/v3/wanted/missing"),
]


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "factory", "root_url", "path"),
    _PAGINATED_CASES,
    ids=[name for name, *_ in _PAGINATED_CASES],
)
async def test_http_500_wraps_to_client_http_error(
    name: str,
    factory: ClientFactory,
    root_url: str,
    path: str,
) -> None:
    """HTTP 500 raises :class:`ClientHTTPError` with the status code in the message."""
    respx.get(f"{root_url}{path}").mock(return_value=httpx.Response(500))
    async with factory(url=root_url, api_key="k") as client:
        with pytest.raises(ClientHTTPError) as exc_info:
            await client.get_wanted_total("missing")
    assert "500" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "factory", "root_url", "path"),
    _PAGINATED_CASES,
    ids=[name for name, *_ in _PAGINATED_CASES],
)
async def test_http_404_wraps_to_client_http_error(
    name: str,
    factory: ClientFactory,
    root_url: str,
    path: str,
) -> None:
    """4xx responses also wrap to :class:`ClientHTTPError` (distinct from transport)."""
    respx.get(f"{root_url}{path}").mock(return_value=httpx.Response(404))
    async with factory(url=root_url, api_key="k") as client:
        with pytest.raises(ClientHTTPError) as exc_info:
            await client.get_wanted_total("missing")
    assert "404" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "factory", "root_url", "path"),
    _PAGINATED_CASES,
    ids=[name for name, *_ in _PAGINATED_CASES],
)
async def test_connect_error_wraps_to_client_transport_error(
    name: str,
    factory: ClientFactory,
    root_url: str,
    path: str,
) -> None:
    """A :class:`httpx.ConnectError` wraps to :class:`ClientTransportError`."""
    respx.get(f"{root_url}{path}").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    async with factory(url=root_url, api_key="k") as client:
        with pytest.raises(ClientTransportError) as exc_info:
            await client.get_wanted_total("missing")
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "factory", "root_url", "path"),
    _PAGINATED_CASES,
    ids=[name for name, *_ in _PAGINATED_CASES],
)
async def test_read_timeout_wraps_to_client_transport_error(
    name: str,
    factory: ClientFactory,
    root_url: str,
    path: str,
) -> None:
    """A :class:`httpx.ReadTimeout` also wraps to :class:`ClientTransportError`."""
    respx.get(f"{root_url}{path}").mock(
        side_effect=httpx.ReadTimeout("read timed out"),
    )
    async with factory(url=root_url, api_key="k") as client:
        with pytest.raises(ClientTransportError) as exc_info:
            await client.get_wanted_total("missing")
    assert isinstance(exc_info.value.__cause__, httpx.ReadTimeout)


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "factory", "root_url", "path"),
    _PAGINATED_CASES,
    ids=[name for name, *_ in _PAGINATED_CASES],
)
async def test_malformed_envelope_wraps_to_client_validation_error(
    name: str,
    factory: ClientFactory,
    root_url: str,
    path: str,
) -> None:
    """An envelope missing ``records`` wraps to :class:`ClientValidationError`.

    ``PaginatedResponse.records`` has no default, so a body that omits
    the field is the cleanest trigger.  ``total_records`` has a default
    of 0 and is not a useful pinning signal here.
    """
    respx.get(f"{root_url}{path}").mock(
        return_value=httpx.Response(200, json={"page": 1}),
    )
    async with factory(url=root_url, api_key="k") as client:
        with pytest.raises(ClientValidationError) as exc_info:
            await client.get_wanted_total("missing")
    # pydantic.ValidationError lives on __cause__.
    assert exc_info.value.__cause__ is not None
    assert "ValidationError" in type(exc_info.value.__cause__).__name__


# Whisparr v3: total computed from /api/v3/movie, not /wanted


_WHISPARR_V3_BASE = "http://whisparr:6969"
_WHISPARR_V3_PATH = "/api/v3/movie"

_WHISPARR_V3_MOVIE_OK: list[dict[str, Any]] = [
    {"id": 1, "title": "A", "monitored": True, "hasFile": False},
]


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_v3_http_500_wraps_to_client_http_error() -> None:
    """Whisparr v3 HTTP 500 on ``/api/v3/movie`` wraps to :class:`ClientHTTPError`."""
    respx.get(f"{_WHISPARR_V3_BASE}{_WHISPARR_V3_PATH}").mock(
        return_value=httpx.Response(500),
    )
    async with WhisparrV3Client(url=_WHISPARR_V3_BASE, api_key="k") as client:
        with pytest.raises(ClientHTTPError) as exc_info:
            await client.get_wanted_total("missing")
    assert "500" in str(exc_info.value)
    assert _WHISPARR_V3_PATH in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_v3_connect_error_wraps_to_client_transport_error() -> None:
    """Whisparr v3 :class:`httpx.ConnectError` wraps to :class:`ClientTransportError`."""
    respx.get(f"{_WHISPARR_V3_BASE}{_WHISPARR_V3_PATH}").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    async with WhisparrV3Client(url=_WHISPARR_V3_BASE, api_key="k") as client:
        with pytest.raises(ClientTransportError) as exc_info:
            await client.get_wanted_total("missing")
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_v3_malformed_movie_wraps_to_client_validation_error() -> None:
    """A malformed movie entry wraps to :class:`ClientValidationError`."""
    # ``id`` is required on WhisparrV3LibraryMovie; missing it trips Pydantic.
    respx.get(f"{_WHISPARR_V3_BASE}{_WHISPARR_V3_PATH}").mock(
        return_value=httpx.Response(200, json=[{"title": "No id", "monitored": True}]),
    )
    async with WhisparrV3Client(url=_WHISPARR_V3_BASE, api_key="k") as client:
        with pytest.raises(ClientValidationError) as exc_info:
            await client.get_wanted_total("missing")
    assert exc_info.value.__cause__ is not None
    assert "ValidationError" in type(exc_info.value.__cause__).__name__


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_v3_cached_success_path_stays_typed_free() -> None:
    """Once the cache is populated subsequent probe calls are pure Python.

    Pinning: the wrap only applies to the first (uncached) call.  After
    that the cache is read synchronously in-process and cannot raise the
    wrapped error types.
    """
    route = respx.get(f"{_WHISPARR_V3_BASE}{_WHISPARR_V3_PATH}").mock(
        return_value=httpx.Response(200, json=_WHISPARR_V3_MOVIE_OK),
    )
    async with WhisparrV3Client(url=_WHISPARR_V3_BASE, api_key="k") as client:
        first = await client.get_wanted_total("missing")
        second = await client.get_wanted_total("missing")
    assert first == 1
    assert second == 1
    # Only one network call happened; the second read came from the cache.
    assert route.call_count == 1

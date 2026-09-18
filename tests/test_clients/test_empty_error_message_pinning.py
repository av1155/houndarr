"""Pin that a timed-out request names the failure instead of trailing off.

``httpx`` maps transport timeouts from a bare ``TimeoutError()``, so
``httpx.ReadTimeout`` and ``httpx.ConnectTimeout`` stringify to the empty
string.  Every client error message that interpolates the exception used
to end at the colon, which is the moment an operator most needs to know
whether the *arr timed out, refused the connection, or answered with
something unparseable.

The other half of the contract matters just as much: an exception that
already carries a message must pass through byte-identical, because
``tests/test_engine/test_typed_errors_pinning.py`` pins that text onto
``search_log.message``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx

from houndarr.clients.base import ArrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from houndarr.errors import ClientTransportError, describe_exception

pytestmark = pytest.mark.pinning

_ROOT = "http://sonarr:8989"
_WV3_ROOT = "http://whisparr3:6969"


def test_empty_message_falls_back_to_the_type_name() -> None:
    """The two timeout classes httpx raises with no message of their own."""
    assert describe_exception(httpx.ReadTimeout("")) == "ReadTimeout"
    assert describe_exception(httpx.ConnectTimeout("")) == "ConnectTimeout"


def test_a_real_message_is_returned_unchanged() -> None:
    """Non-empty text passes through byte-identical; the typed-error tests pin it."""
    assert describe_exception(httpx.ConnectError("All connection attempts failed")) == (
        "All connection attempts failed"
    )
    assert describe_exception(ValueError("boom")) == "boom"


# Each entry drives one of the interpolating call sites.
_CALLS: list[tuple[str, str, Callable[[ArrClient], Awaitable[Any]]]] = [
    ("queue-status", SonarrClient._QUEUE_STATUS_PATH, lambda c: c.get_queue_status()),
    ("queue-details", SonarrClient._QUEUE_DETAILS_PATH, lambda c: c.get_queue_item_ids()),
    ("tags", SonarrClient._TAG_PATH, lambda c: c.get_tags()),
    ("wanted-total", "/api/v3/wanted/missing", lambda c: c.get_wanted_total("missing")),
]


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "path", "call"),
    _CALLS,
    ids=[name for name, *_ in _CALLS],
)
async def test_a_timeout_names_itself(
    name: str,
    path: str,
    call: Callable[[ArrClient], Awaitable[Any]],
) -> None:
    """A timeout with no message of its own still reaches the operator named."""
    respx.get(f"{_ROOT}{path}").mock(side_effect=httpx.ReadTimeout(""))
    async with SonarrClient(url=_ROOT, api_key="k") as client:
        with pytest.raises(ClientTransportError) as exc_info:
            await call(client)
    message = str(exc_info.value)
    assert message.endswith("ReadTimeout")
    assert not message.endswith(": ")
    assert isinstance(exc_info.value.__cause__, httpx.ReadTimeout)


@pytest.mark.asyncio()
@respx.mock
async def test_the_whisparr_v3_total_override_names_a_timeout() -> None:
    """Whisparr v3 computes its total from /movie and wraps that call itself."""
    respx.get(f"{_WV3_ROOT}/api/v3/movie").mock(side_effect=httpx.ReadTimeout(""))
    async with WhisparrV3Client(url=_WV3_ROOT, api_key="k") as client:
        with pytest.raises(ClientTransportError) as exc_info:
            await client.get_wanted_total("missing")
    assert str(exc_info.value).endswith("ReadTimeout")


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(
    ("name", "path", "call"),
    _CALLS,
    ids=[name for name, *_ in _CALLS],
)
async def test_an_error_that_has_a_message_keeps_it(
    name: str,
    path: str,
    call: Callable[[ArrClient], Awaitable[Any]],
) -> None:
    """The fallback must not rewrite text an exception already carries."""
    respx.get(f"{_ROOT}{path}").mock(side_effect=httpx.ConnectError("connection refused"))
    async with SonarrClient(url=_ROOT, api_key="k") as client:
        with pytest.raises(ClientTransportError) as exc_info:
            await call(client)
    message = str(exc_info.value)
    assert message.endswith("connection refused")
    assert "ConnectError" not in message

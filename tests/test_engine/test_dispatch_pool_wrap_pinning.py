"""Pin the typed-error wrap helpers for dispatch + upgrade pool fetch.

The search loop narrows the dispatch and pool-fetch
``except Exception`` branches onto the typed
:class:`~houndarr.errors.EngineDispatchError` /
:class:`~houndarr.errors.EnginePoolFetchError` surface via two
helpers in :mod:`houndarr.engine.search_loop`:

* :func:`_dispatch_with_typed_wrap`: owns the
  ``adapter.make_client`` -> dispatch attempt boundary.
* :func:`_fetch_pool_with_typed_wrap`: owns the
  ``adapter.make_client`` -> fetch_upgrade_pool boundary.

These tests lock the wrap contract end-to-end: message preservation,
``__cause__`` chain, and passthrough of already-typed errors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from houndarr.engine.adapters import AppAdapter
from houndarr.engine.candidates import SearchCandidate
from houndarr.engine.search_loop import (
    _dispatch_with_typed_wrap,
    _fetch_pool_with_typed_wrap,
)
from houndarr.enums import ItemType
from houndarr.errors import (
    ClientHTTPError,
    ClientTransportError,
    ClientValidationError,
    EngineDispatchError,
    EngineError,
    EnginePoolFetchError,
)
from tests.test_engine.conftest import make_instance

pytestmark = pytest.mark.pinning


def _fake_candidate() -> SearchCandidate:
    """Build a minimal SearchCandidate for dispatch-wrap tests."""
    return SearchCandidate(
        item_id=101,
        item_type=ItemType.episode,
        label="Test / S01E01",
        unreleased_reason=None,
        group_key=None,
        search_payload={},
    )


def _adapter_with_fake_client(side_effect: Any = None, return_value: Any = None) -> AppAdapter:
    """Build an AppAdapter whose make_client yields a mocked context.

    The make_client callable returns an async context manager; entering
    it yields a stub client.  dispatch_fn / fetch_upgrade_pool are
    attached with the requested side_effect or return_value so each
    wrap path can be exercised independently.
    """

    stub_client = MagicMock()

    class _CtxManager:
        async def __aenter__(self) -> Any:
            return stub_client

        async def __aexit__(self, *args: object) -> None:
            return None

    adapter = MagicMock(spec=AppAdapter)
    adapter.make_client = MagicMock(return_value=_CtxManager())
    if side_effect is not None:
        adapter.fetch_upgrade_pool = AsyncMock(side_effect=side_effect)
    else:
        adapter.fetch_upgrade_pool = AsyncMock(return_value=return_value or [])
    return adapter


# _dispatch_with_typed_wrap


class TestDispatchTypedWrap:
    """Pin the dispatch-side wrap helper."""

    @pytest.mark.asyncio()
    async def test_http_status_error_wraps_to_engine_dispatch_error(self) -> None:
        """A raw httpx.HTTPStatusError becomes EngineDispatchError."""
        adapter = _adapter_with_fake_client()
        request = httpx.Request("POST", "http://sonarr:8989/api/v3/command")
        response = httpx.Response(500, request=request)
        original = httpx.HTTPStatusError("server error", request=request, response=response)
        dispatch_fn = AsyncMock(side_effect=original)

        with pytest.raises(EngineDispatchError) as exc_info:
            await _dispatch_with_typed_wrap(
                adapter, make_instance(), dispatch_fn, _fake_candidate()
            )

        assert exc_info.value.__cause__ is original
        # Message preservation: str(typed) == str(original) keeps the
        # golden search_log.message byte-equal.
        assert str(exc_info.value) == str(original)

    @pytest.mark.asyncio()
    async def test_key_error_wraps_to_engine_dispatch_error(self) -> None:
        """An unrelated Exception (e.g. KeyError) also wraps."""
        adapter = _adapter_with_fake_client()
        original = KeyError("boom")
        dispatch_fn = AsyncMock(side_effect=original)

        with pytest.raises(EngineDispatchError) as exc_info:
            await _dispatch_with_typed_wrap(
                adapter, make_instance(), dispatch_fn, _fake_candidate()
            )

        assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "cls",
        [ClientHTTPError, ClientTransportError, ClientValidationError],
    )
    async def test_client_error_subclasses_propagate_unchanged(
        self, cls: type[EngineError]
    ) -> None:
        """ClientError subclasses pass through unchanged (not re-wrapped)."""
        adapter = _adapter_with_fake_client()
        original = cls("client layer failure")
        dispatch_fn = AsyncMock(side_effect=original)

        with pytest.raises(cls) as exc_info:
            await _dispatch_with_typed_wrap(
                adapter, make_instance(), dispatch_fn, _fake_candidate()
            )

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_engine_error_propagates_unchanged(self) -> None:
        """An already-typed EngineError is not re-wrapped."""
        adapter = _adapter_with_fake_client()
        original = EngineError("inner engine error")
        dispatch_fn = AsyncMock(side_effect=original)

        with pytest.raises(EngineError) as exc_info:
            await _dispatch_with_typed_wrap(
                adapter, make_instance(), dispatch_fn, _fake_candidate()
            )

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_success_returns_none(self) -> None:
        """Happy path: the helper returns None and dispatch_fn is awaited."""
        adapter = _adapter_with_fake_client()
        dispatch_fn = AsyncMock(return_value=None)

        result = await _dispatch_with_typed_wrap(
            adapter, make_instance(), dispatch_fn, _fake_candidate()
        )

        assert result is None
        dispatch_fn.assert_awaited_once()


# _fetch_pool_with_typed_wrap


class TestFetchPoolTypedWrap:
    """Pin the upgrade-pool-fetch-side wrap helper."""

    @pytest.mark.asyncio()
    async def test_http_status_error_wraps_to_engine_pool_fetch_error(self) -> None:
        """A raw httpx.HTTPStatusError becomes EnginePoolFetchError."""
        request = httpx.Request("GET", "http://radarr:7878/api/v3/movie")
        response = httpx.Response(500, request=request)
        original = httpx.HTTPStatusError("server error", request=request, response=response)
        adapter = _adapter_with_fake_client(side_effect=original)

        with pytest.raises(EnginePoolFetchError) as exc_info:
            await _fetch_pool_with_typed_wrap(adapter, make_instance())

        assert exc_info.value.__cause__ is original
        assert str(exc_info.value) == str(original)

    @pytest.mark.asyncio()
    async def test_runtime_error_wraps(self) -> None:
        """Any Exception (not just httpx) is wrapped."""
        original = RuntimeError("pool builder crashed")
        adapter = _adapter_with_fake_client(side_effect=original)

        with pytest.raises(EnginePoolFetchError) as exc_info:
            await _fetch_pool_with_typed_wrap(adapter, make_instance())

        assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "cls",
        [ClientHTTPError, ClientTransportError, ClientValidationError],
    )
    async def test_client_error_subclasses_propagate_unchanged(
        self, cls: type[EngineError]
    ) -> None:
        """ClientError subclasses pass through; the wrap does not re-type them."""
        original = cls("client layer failure")
        adapter = _adapter_with_fake_client(side_effect=original)

        with pytest.raises(cls) as exc_info:
            await _fetch_pool_with_typed_wrap(adapter, make_instance())

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_engine_error_propagates_unchanged(self) -> None:
        """Already-typed EngineError passes through."""
        original = EngineError("inner engine error")
        adapter = _adapter_with_fake_client(side_effect=original)

        with pytest.raises(EngineError) as exc_info:
            await _fetch_pool_with_typed_wrap(adapter, make_instance())

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_success_returns_adapter_pool_verbatim(self) -> None:
        """Happy path: return whatever fetch_upgrade_pool produced."""
        pool_items = [object(), object(), object()]
        adapter = _adapter_with_fake_client(return_value=pool_items)

        result = await _fetch_pool_with_typed_wrap(adapter, make_instance())

        assert result == pool_items

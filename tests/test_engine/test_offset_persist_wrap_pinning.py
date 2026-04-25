"""Pin the typed-error wrap on :func:`_persist_offset_with_typed_wrap`.

The engine narrows the four offset-persist ``except Exception``
branches around ``update_instance(...)`` onto the typed
:class:`~houndarr.errors.EngineOffsetPersistError` surface via
:func:`_persist_offset_with_typed_wrap` in
:mod:`houndarr.engine.search_loop`.

These tests lock:

* arbitrary ``Exception`` from ``update_instance`` wraps to
  :class:`EngineOffsetPersistError` with ``__cause__`` preserved and
  the original ``str`` kept verbatim.
* already-typed Houndarr errors (:class:`EngineError`,
  :class:`ClientError`) propagate unchanged.
* the happy path forwards the kwargs to ``update_instance`` without
  raising.

Non-fatal by design: the four search-loop call sites swallow the
typed error and log; the next cycle retries the persist.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from houndarr.engine import search_loop as search_loop_module
from houndarr.engine.search_loop import _persist_offset_with_typed_wrap
from houndarr.errors import (
    ClientHTTPError,
    ClientTransportError,
    ClientValidationError,
    EngineError,
    EngineOffsetPersistError,
)

pytestmark = pytest.mark.pinning


_MASTER_KEY = b"0123456789abcdef0123456789abcdef_"


class TestPersistOffsetTypedWrap:
    """Pin the offset-persist wrap helper."""

    @pytest.mark.asyncio()
    async def test_arbitrary_exception_wraps_to_engine_offset_persist_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A RuntimeError from update_instance wraps to EngineOffsetPersistError."""
        original = RuntimeError("db write failed")
        monkeypatch.setattr(
            search_loop_module,
            "update_instance",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(EngineOffsetPersistError) as exc_info:
            await _persist_offset_with_typed_wrap(
                1,
                master_key=_MASTER_KEY,
                missing_page_offset=3,
            )

        assert exc_info.value.__cause__ is original
        assert str(exc_info.value) == str(original)

    @pytest.mark.asyncio()
    async def test_operational_error_wraps(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An aiosqlite-style OperationalError also wraps."""

        class OperationalError(Exception):
            """Stand-in for aiosqlite.OperationalError."""

        original = OperationalError("database locked")
        monkeypatch.setattr(
            search_loop_module,
            "update_instance",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(EngineOffsetPersistError) as exc_info:
            await _persist_offset_with_typed_wrap(
                1,
                master_key=_MASTER_KEY,
                upgrade_item_offset=2,
            )

        assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "cls",
        [ClientHTTPError, ClientTransportError, ClientValidationError],
    )
    async def test_client_error_subclasses_propagate_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cls: type[EngineError],
    ) -> None:
        """ClientError subclasses pass through unchanged (no re-wrap)."""
        original = cls("client layer")
        monkeypatch.setattr(
            search_loop_module,
            "update_instance",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(cls) as exc_info:
            await _persist_offset_with_typed_wrap(
                1,
                master_key=_MASTER_KEY,
                cutoff_page_offset=4,
            )

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_engine_error_propagates_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An already-typed EngineError is not re-wrapped."""
        original = EngineError("inner engine error")
        monkeypatch.setattr(
            search_loop_module,
            "update_instance",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(EngineError) as exc_info:
            await _persist_offset_with_typed_wrap(
                1,
                master_key=_MASTER_KEY,
                upgrade_series_offset=5,
            )

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_success_forwards_kwargs_to_update_instance(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path forwards every kwarg to update_instance verbatim."""
        mock_update = AsyncMock(return_value=None)
        monkeypatch.setattr(search_loop_module, "update_instance", mock_update)

        await _persist_offset_with_typed_wrap(
            7,
            master_key=_MASTER_KEY,
            missing_page_offset=11,
            upgrade_item_offset=13,
        )

        mock_update.assert_awaited_once_with(
            7,
            master_key=_MASTER_KEY,
            missing_page_offset=11,
            upgrade_item_offset=13,
        )

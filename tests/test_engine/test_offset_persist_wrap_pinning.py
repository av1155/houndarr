"""Pin the typed-error wrap on :func:`_persist_offset_with_typed_wrap`.

The engine narrows the four offset-persist ``except Exception``
branches around ``update_instance_offset(...)`` onto the typed
:class:`~houndarr.errors.EngineOffsetPersistError` surface via
:func:`_persist_offset_with_typed_wrap` in
:mod:`houndarr.engine.search_loop`.

These tests lock:

* arbitrary ``Exception`` from ``update_instance_offset`` wraps to
  :class:`EngineOffsetPersistError` with ``__cause__`` preserved and
  the original ``str`` kept verbatim.
* already-typed Houndarr errors (:class:`EngineError`,
  :class:`ClientError`) propagate unchanged.
* the happy path forwards the instance id, column, start and value.
* a write the row no longer matches is skipped quietly: no raise, no
  WARNING.

Non-fatal by design: the four search-loop call sites swallow the
typed error and log; the next cycle retries the persist.
"""

from __future__ import annotations

import logging
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

from .conftest import make_instance

pytestmark = pytest.mark.pinning


class TestPersistOffsetTypedWrap:
    """Pin the offset-persist wrap helper."""

    @pytest.mark.asyncio()
    async def test_arbitrary_exception_wraps_to_engine_offset_persist_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A RuntimeError from update_instance_offset wraps to EngineOffsetPersistError."""
        original = RuntimeError("db write failed")
        monkeypatch.setattr(
            search_loop_module,
            "update_instance_offset",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(EngineOffsetPersistError) as exc_info:
            await _persist_offset_with_typed_wrap(
                make_instance(),
                "missing_page_offset",
                start=2,
                value=3,
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
            "update_instance_offset",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(EngineOffsetPersistError) as exc_info:
            await _persist_offset_with_typed_wrap(
                make_instance(),
                "upgrade_item_offset",
                start=1,
                value=2,
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
            "update_instance_offset",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(cls) as exc_info:
            await _persist_offset_with_typed_wrap(
                make_instance(),
                "cutoff_page_offset",
                start=3,
                value=4,
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
            "update_instance_offset",
            AsyncMock(side_effect=original),
        )

        with pytest.raises(EngineError) as exc_info:
            await _persist_offset_with_typed_wrap(
                make_instance(),
                "upgrade_series_offset",
                start=0,
                value=5,
            )

        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_success_forwards_the_conditional_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path forwards the id, column, start and value verbatim."""
        mock_update = AsyncMock(return_value=True)
        monkeypatch.setattr(search_loop_module, "update_instance_offset", mock_update)

        await _persist_offset_with_typed_wrap(
            make_instance(instance_id=7),
            "missing_page_offset",
            start=10,
            value=11,
        )

        mock_update.assert_awaited_once_with(7, "missing_page_offset", expected=10, value=11)

    @pytest.mark.asyncio()
    async def test_a_skipped_write_logs_at_debug_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A row that changed during the cycle keeps its value without a warning."""
        monkeypatch.setattr(
            search_loop_module,
            "update_instance_offset",
            AsyncMock(return_value=False),
        )

        with caplog.at_level(logging.DEBUG, logger="houndarr.engine.search_loop"):
            await _persist_offset_with_typed_wrap(
                make_instance(),
                "upgrade_item_offset",
                start=4,
                value=5,
            )

        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (
                logging.DEBUG,
                "[Test Instance] upgrade_item_offset changed during the cycle;"
                " keeping the stored value",
            ),
        ]

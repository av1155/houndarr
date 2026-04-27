"""Pin the typed-error surface on ``run_instance_search`` and the supervisor.

:func:`run_instance_search` carries a top-level wrap that re-raises
typed errors unchanged and converts any other ``Exception`` into a
fresh :class:`EngineError` with the original cause preserved on
``__cause__``.  The supervisor's ``_run_search_cycle`` catches
``(EngineError, ClientError)`` to match.

These tests lock both ends of that contract:

* :func:`run_instance_search` wraps untyped exceptions and lets the
  three escape types (``EngineError``, ``ClientError``,
  ``httpx.TransportError``) propagate unchanged.
* The supervisor's ``_run_search_cycle`` writes a single error row
  for ``EngineError`` and ``ClientError`` and still returns ``True``
  on ``httpx.TransportError`` so the reconnect loop engages.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine import search_loop as search_loop_module
from houndarr.engine import supervisor as supervisor_module
from houndarr.engine.search_loop import run_instance_search
from houndarr.engine.supervisor import Supervisor
from houndarr.errors import (
    ClientError,
    ClientHTTPError,
    ClientTransportError,
    ClientValidationError,
    EngineError,
)
from houndarr.services.instances import InstanceType
from tests.test_engine.conftest import SONARR_URL, make_instance

pytestmark = pytest.mark.pinning


_MASTER_KEY: bytes = Fernet.generate_key()


def _encrypt_key(value: str) -> str:
    """Return a Fernet token usable in the ``instances`` table seed rows."""
    return Fernet(_MASTER_KEY).encrypt(value.encode()).decode()


# run_instance_search wrap surface


class TestRunInstanceSearchWrap:
    """Pin the public entrypoint's typed-error surface."""

    @pytest.mark.asyncio()
    async def test_unhandled_exception_wraps_to_engine_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A raw ``Exception`` escaping the cycle body is wrapped in EngineError."""
        original = RuntimeError("boom")
        monkeypatch.setattr(
            search_loop_module,
            "_run_instance_search_impl",
            AsyncMock(side_effect=original),
        )
        instance = make_instance()
        with pytest.raises(EngineError) as exc_info:
            await run_instance_search(instance, _MASTER_KEY)
        assert exc_info.value.__cause__ is original
        assert instance.core.name in str(exc_info.value)

    @pytest.mark.asyncio()
    async def test_key_error_wraps_to_engine_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KeyError (e.g. unknown instance type) also wraps to EngineError."""
        original = KeyError("unknown-type")
        monkeypatch.setattr(
            search_loop_module,
            "_run_instance_search_impl",
            AsyncMock(side_effect=original),
        )
        instance = make_instance()
        with pytest.raises(EngineError) as exc_info:
            await run_instance_search(instance, _MASTER_KEY)
        assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio()
    async def test_engine_error_propagates_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An already-typed EngineError passes through the wrapper unchanged."""
        original = EngineError("dispatch failed")
        monkeypatch.setattr(
            search_loop_module,
            "_run_instance_search_impl",
            AsyncMock(side_effect=original),
        )
        instance = make_instance()
        with pytest.raises(EngineError) as exc_info:
            await run_instance_search(instance, _MASTER_KEY)
        # Same instance, not wrapped again.
        assert exc_info.value is original

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "cls",
        [ClientHTTPError, ClientTransportError, ClientValidationError],
    )
    async def test_client_error_subclasses_propagate_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cls: type[ClientError],
    ) -> None:
        """All three ClientError subclasses propagate without re-wrap."""
        original = cls("client layer failure")
        monkeypatch.setattr(
            search_loop_module,
            "_run_instance_search_impl",
            AsyncMock(side_effect=original),
        )
        instance = make_instance()
        with pytest.raises(cls) as exc_info:
            await run_instance_search(instance, _MASTER_KEY)
        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_httpx_transport_error_propagates_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """httpx.TransportError bypasses the wrap so the supervisor sees it."""
        original = httpx.ConnectError("connection refused")
        monkeypatch.setattr(
            search_loop_module,
            "_run_instance_search_impl",
            AsyncMock(side_effect=original),
        )
        instance = make_instance()
        with pytest.raises(httpx.ConnectError) as exc_info:
            await run_instance_search(instance, _MASTER_KEY)
        assert exc_info.value is original

    @pytest.mark.asyncio()
    async def test_successful_run_returns_impl_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path: the wrapper returns whatever the impl returned."""
        monkeypatch.setattr(
            search_loop_module,
            "_run_instance_search_impl",
            AsyncMock(return_value=7),
        )
        instance = make_instance()
        result = await run_instance_search(instance, _MASTER_KEY)
        assert result == 7


# Supervisor._run_search_cycle catch surface


@pytest_asyncio.fixture()
async def seeded_supervisor_instance(db: None) -> AsyncGenerator[None, None]:
    """Seed one enabled Sonarr instance keyed by FK constraints on search_log."""
    enc = _encrypt_key("test-api-key")
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO instances
            (id, name, type, url, encrypted_api_key, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1, "Sonarr Test", "sonarr", SONARR_URL, enc, 1),
        )
        await conn.commit()
    yield


async def _fetch_error_rows() -> list[dict[str, Any]]:
    """Return every ``action='error'`` row in search_log ordered by id."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM search_log WHERE action = 'error' ORDER BY id ASC"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class TestSupervisorCycleCatchSurface:
    """Pin ``Supervisor._run_search_cycle`` typed-error catch behaviour."""

    @pytest.mark.asyncio()
    async def test_engine_error_writes_row_and_returns_false(
        self,
        seeded_supervisor_instance: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """EngineError from run_instance_search produces one error row, returns False."""
        monkeypatch.setattr(
            supervisor_module,
            "run_instance_search",
            AsyncMock(side_effect=EngineError("bad cycle")),
        )
        sup = Supervisor(master_key=_MASTER_KEY)
        instance = make_instance(instance_id=1)

        result = await sup._run_search_cycle(instance, cycle_trigger="scheduled")

        assert result is False
        rows = await _fetch_error_rows()
        assert len(rows) == 1
        assert rows[0]["instance_id"] == 1
        assert rows[0]["action"] == "error"
        assert rows[0]["cycle_trigger"] == "scheduled"
        assert rows[0]["message"] == "bad cycle"

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "cls",
        [ClientHTTPError, ClientTransportError, ClientValidationError],
    )
    async def test_client_error_writes_row_and_returns_false(
        self,
        seeded_supervisor_instance: None,
        monkeypatch: pytest.MonkeyPatch,
        cls: type[ClientError],
    ) -> None:
        """Every ClientError subclass lands on the same supervisor branch."""
        monkeypatch.setattr(
            supervisor_module,
            "run_instance_search",
            AsyncMock(side_effect=cls("client layer")),
        )
        sup = Supervisor(master_key=_MASTER_KEY)
        instance = make_instance(instance_id=1)

        result = await sup._run_search_cycle(instance, cycle_trigger="run_now")

        assert result is False
        rows = await _fetch_error_rows()
        assert len(rows) == 1
        assert rows[0]["instance_id"] == 1
        assert rows[0]["cycle_trigger"] == "run_now"
        assert rows[0]["message"] == "client layer"

    @pytest.mark.asyncio()
    async def test_transport_error_returns_true_and_writes_nothing(
        self,
        seeded_supervisor_instance: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """httpx.TransportError triggers the reconnect branch and skips the row.

        Pinning: the row write happens in the outer reconnect loop the
        FIRST time a connection error is seen, not here.  This branch
        only signals the outer loop via the ``True`` return value.
        """
        monkeypatch.setattr(
            supervisor_module,
            "run_instance_search",
            AsyncMock(side_effect=httpx.ConnectError("refused")),
        )
        sup = Supervisor(master_key=_MASTER_KEY)
        instance = make_instance(instance_id=1)

        result = await sup._run_search_cycle(instance, cycle_trigger="scheduled")

        assert result is True
        rows = await _fetch_error_rows()
        assert rows == []

    @pytest.mark.asyncio()
    async def test_success_returns_false_and_writes_nothing(
        self,
        seeded_supervisor_instance: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path: run_instance_search returns, cycle returns False, no row."""
        monkeypatch.setattr(
            supervisor_module,
            "run_instance_search",
            AsyncMock(return_value=3),
        )
        sup = Supervisor(master_key=_MASTER_KEY)
        instance = make_instance(instance_id=1, itype=InstanceType.sonarr)

        result = await sup._run_search_cycle(instance, cycle_trigger="scheduled")

        assert result is False
        rows = await _fetch_error_rows()
        assert rows == []

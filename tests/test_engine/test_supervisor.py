"""Tests for the Supervisor engine — connection-error deduplication and startup grace."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine import supervisor as _supervisor_mod
from houndarr.engine.supervisor import Supervisor
from houndarr.services.instances import Instance, InstanceType, SonarrSearchMode

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SONARR_URL = "http://sonarr:8989"
MASTER_KEY: bytes = Fernet.generate_key()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_instance(
    *,
    instance_id: int = 1,
    url: str = SONARR_URL,
    enabled: bool = True,
    sleep_interval_mins: int = 30,
) -> Instance:
    return Instance(
        id=instance_id,
        name="Test Sonarr",
        type=InstanceType.sonarr,
        url=url,
        api_key="test-api-key",
        enabled=enabled,
        batch_size=2,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=4,
        cooldown_days=14,
        post_release_grace_hrs=6,
        cutoff_enabled=False,
        cutoff_batch_size=1,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        sonarr_search_mode=SonarrSearchMode.episode,
    )


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed FK-required instance rows so search_log can reference them."""
    from houndarr.crypto import encrypt

    encrypted = encrypt("test-api-key", MASTER_KEY)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            (1, "Test Sonarr", "sonarr", SONARR_URL, encrypted),
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Helper: fetch all search_log rows
# ---------------------------------------------------------------------------


async def _get_log_rows() -> list[dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM search_log ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests — startup grace delay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_instance_loop_waits_startup_grace_before_first_cycle(
    seeded_instances: None,
) -> None:
    """_instance_loop sleeps _STARTUP_GRACE_SECS before the first search cycle."""
    from houndarr.engine.supervisor import _STARTUP_GRACE_SECS

    instance = _make_instance()
    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        # Cancel after the first sleep so the loop exits cleanly.
        raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "_STARTUP_GRACE_SECS", 10),
        patch("houndarr.engine.supervisor.get_instance", return_value=instance),
        patch("houndarr.engine.supervisor.asyncio.sleep", side_effect=fake_sleep),
        patch(
            "houndarr.engine.supervisor.run_instance_search",
            new_callable=AsyncMock,
        ) as mock_search,
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        with pytest.raises(asyncio.CancelledError):  # noqa: PT012
            await supervisor._instance_loop(instance.id)  # noqa: SLF001

    # The very first sleep must be the startup grace, not the inter-cycle sleep.
    assert sleep_calls, "expected at least one asyncio.sleep call"
    assert sleep_calls[0] == _STARTUP_GRACE_SECS
    # No search should have run because CancelledError fires in the first sleep.
    mock_search.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — state-transition error logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_first_connect_error_writes_exactly_one_error_row(
    seeded_instances: None,
) -> None:
    """The first TransportError in a sequence writes exactly one error log row."""
    instance = _make_instance()
    call_count = 0

    async def fail_once_then_cancel(*_: Any, **__: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TransportError("refused")
        raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "_STARTUP_GRACE_SECS", 0),
        patch("houndarr.engine.supervisor.get_instance", return_value=instance),
        patch("houndarr.engine.supervisor.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "houndarr.engine.supervisor.run_instance_search",
            side_effect=fail_once_then_cancel,
        ),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        with pytest.raises(asyncio.CancelledError):
            await supervisor._instance_loop(instance.id)  # noqa: SLF001

    rows = await _get_log_rows()
    error_rows = [r for r in rows if r["action"] == "error"]
    assert len(error_rows) == 1
    assert SONARR_URL in (error_rows[0]["message"] or "")


@pytest.mark.asyncio()
async def test_repeated_connect_errors_write_only_one_error_row(
    seeded_instances: None,
) -> None:
    """Multiple consecutive TransportErrors produce exactly one error log row."""
    instance = _make_instance()
    call_count = 0

    async def fail_three_then_cancel(*_: Any, **__: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise httpx.TransportError("refused")
        raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "_STARTUP_GRACE_SECS", 0),
        patch("houndarr.engine.supervisor.get_instance", return_value=instance),
        patch("houndarr.engine.supervisor.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "houndarr.engine.supervisor.run_instance_search",
            side_effect=fail_three_then_cancel,
        ),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        with pytest.raises(asyncio.CancelledError):
            await supervisor._instance_loop(instance.id)  # noqa: SLF001

    rows = await _get_log_rows()
    error_rows = [r for r in rows if r["action"] == "error"]
    assert len(error_rows) == 1, (
        f"expected 1 error row for {call_count} retries, got {len(error_rows)}"
    )


@pytest.mark.asyncio()
async def test_recovery_after_connect_error_writes_info_row(
    seeded_instances: None,
) -> None:
    """After reconnecting, exactly one action='info' recovery row is written."""
    instance = _make_instance()
    call_count = 0

    async def fail_once_then_succeed(*_: Any, **__: Any) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TransportError("refused")
        if call_count == 2:
            return 0  # success — triggers recovery path
        raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "_STARTUP_GRACE_SECS", 0),
        patch("houndarr.engine.supervisor.get_instance", return_value=instance),
        patch("houndarr.engine.supervisor.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "houndarr.engine.supervisor.run_instance_search",
            side_effect=fail_once_then_succeed,
        ),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        with pytest.raises(asyncio.CancelledError):
            await supervisor._instance_loop(instance.id)  # noqa: SLF001

    rows = await _get_log_rows()
    info_rows = [r for r in rows if r["action"] == "info"]
    assert any("reachable" in (r["message"] or "") for r in info_rows), (
        f"expected a recovery info row, got: {rows}"
    )


@pytest.mark.asyncio()
async def test_no_extra_log_rows_during_retry_sequence(
    seeded_instances: None,
) -> None:
    """Retries between first failure and recovery produce no additional log rows."""
    instance = _make_instance()
    call_count = 0

    async def fail_twice_then_succeed(*_: Any, **__: Any) -> int:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise httpx.TransportError("refused")
        if call_count == 3:
            return 0  # recovery
        raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "_STARTUP_GRACE_SECS", 0),
        patch("houndarr.engine.supervisor.get_instance", return_value=instance),
        patch("houndarr.engine.supervisor.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "houndarr.engine.supervisor.run_instance_search",
            side_effect=fail_twice_then_succeed,
        ),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        with pytest.raises(asyncio.CancelledError):
            await supervisor._instance_loop(instance.id)  # noqa: SLF001

    rows = await _get_log_rows()
    # Expect exactly: 1 error (first failure) + 1 info (recovery) = 2 rows total
    assert len(rows) == 2, f"expected 2 log rows (1 error + 1 recovery), got: {rows}"
    assert rows[0]["action"] == "error"
    assert rows[1]["action"] == "info"

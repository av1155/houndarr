"""Tests for Supervisor edge cases: lifecycle, run-now, connection recovery."""

from __future__ import annotations

import asyncio
import contextlib
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
from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    MissingPolicy,
    RuntimeSnapshot,
    SchedulePolicy,
    SearchOrder,
    SonarrSearchMode,
    UpgradePolicy,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SONARR_URL = "http://sonarr:8989"
MASTER_KEY: bytes = Fernet.generate_key()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instance(
    *,
    instance_id: int = 1,
    url: str = SONARR_URL,
    enabled: bool = True,
    sleep_interval_mins: int = 30,
) -> Instance:
    return Instance(
        core=InstanceCore(
            id=instance_id,
            name="Test Sonarr",
            type=InstanceType.sonarr,
            url=url,
            api_key="test-api-key",
            enabled=enabled,
        ),
        missing=MissingPolicy(
            batch_size=2,
            sleep_interval_mins=sleep_interval_mins,
            hourly_cap=4,
            cooldown_days=14,
            post_release_grace_hrs=6,
            queue_limit=0,
            sonarr_search_mode=SonarrSearchMode.episode,
        ),
        cutoff=CutoffPolicy(
            cutoff_enabled=False,
            cutoff_batch_size=1,
            cutoff_cooldown_days=21,
            cutoff_hourly_cap=1,
        ),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(search_order=SearchOrder.chronological),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
    )


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed FK-required instance rows."""
    from houndarr.crypto import encrypt

    encrypted = encrypt("test-api-key", MASTER_KEY)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            (1, "Test Sonarr", "sonarr", SONARR_URL, encrypted),
        )
        await conn.commit()
    yield


async def _get_log_rows() -> list[dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM search_log ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# trigger_run_now tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_run_now_accepted_for_enabled_instance(seeded_instances: None) -> None:
    """trigger_run_now returns 'accepted' for an enabled instance."""
    supervisor = Supervisor(master_key=MASTER_KEY)
    with patch.object(supervisor, "_run_search_cycle", new_callable=AsyncMock, return_value=False):
        result = await supervisor.trigger_run_now(1)
    assert result == "accepted"


@pytest.mark.asyncio()
async def test_run_now_disabled_returns_disabled(seeded_instances: None) -> None:
    """trigger_run_now returns 'disabled' when the instance is not enabled."""
    async with get_db() as conn:
        await conn.execute("UPDATE instances SET enabled = 0 WHERE id = 1")
        await conn.commit()

    supervisor = Supervisor(master_key=MASTER_KEY)
    result = await supervisor.trigger_run_now(1)
    assert result == "disabled"


@pytest.mark.asyncio()
async def test_run_now_not_found_returns_not_found(seeded_instances: None) -> None:
    """trigger_run_now returns 'not_found' for a nonexistent instance."""
    supervisor = Supervisor(master_key=MASTER_KEY)
    result = await supervisor.trigger_run_now(999)
    assert result == "not_found"


@pytest.mark.asyncio()
async def test_run_now_duplicate_returns_accepted(seeded_instances: None) -> None:
    """A second trigger_run_now for the same instance returns 'accepted' without double-task."""
    supervisor = Supervisor(master_key=MASTER_KEY)
    long_running = asyncio.Event()

    async def _slow_cycle(*args: object, **kwargs: object) -> bool:
        await long_running.wait()
        return False

    with patch.object(supervisor, "_run_search_cycle", side_effect=_slow_cycle):
        r1 = await supervisor.trigger_run_now(1)
        r2 = await supervisor.trigger_run_now(1)

    assert r1 == "accepted"
    assert r2 == "accepted"
    # Only one manual task should exist
    assert len(supervisor._manual_runs) <= 1  # noqa: SLF001

    # Cleanup: release the event and cancel tasks
    long_running.set()
    for task in list(supervisor._manual_runs.values()):  # noqa: SLF001
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# start_instance_task tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_start_instance_task_idempotent(seeded_instances: None) -> None:
    """Calling start_instance_task twice returns False the second time."""
    supervisor = Supervisor(master_key=MASTER_KEY)

    # Patch the loop so it waits indefinitely
    async def _wait_forever(self: object, iid: int, startup_offset: int = 0) -> None:
        await asyncio.Event().wait()

    with patch.object(Supervisor, "_instance_loop", _wait_forever):
        first = await supervisor.start_instance_task(1)
        second = await supervisor.start_instance_task(1)

    assert first is True
    assert second is False

    await supervisor.stop()


@pytest.mark.asyncio()
async def test_start_instance_task_disabled_returns_false(seeded_instances: None) -> None:
    """start_instance_task returns False for a disabled instance."""
    async with get_db() as conn:
        await conn.execute("UPDATE instances SET enabled = 0 WHERE id = 1")
        await conn.commit()

    supervisor = Supervisor(master_key=MASTER_KEY)
    result = await supervisor.start_instance_task(1)
    assert result is False


# ---------------------------------------------------------------------------
# stop_instance_task tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_stop_instance_task_cancels_task(seeded_instances: None) -> None:
    """stop_instance_task cancels a running scheduled task."""
    supervisor = Supervisor(master_key=MASTER_KEY)

    async def _wait_forever(self: object, iid: int, startup_offset: int = 0) -> None:
        await asyncio.Event().wait()

    with patch.object(Supervisor, "_instance_loop", _wait_forever):
        await supervisor.start_instance_task(1)
        result = await supervisor.stop_instance_task(1)

    assert result is True
    assert 1 not in supervisor._tasks  # noqa: SLF001


# ---------------------------------------------------------------------------
# reconcile_instance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_reconcile_starts_for_enabled(seeded_instances: None) -> None:
    """reconcile_instance starts a task for an enabled instance."""
    supervisor = Supervisor(master_key=MASTER_KEY)

    async def _wait_forever(self: object, iid: int, startup_offset: int = 0) -> None:
        await asyncio.Event().wait()

    with patch.object(Supervisor, "_instance_loop", _wait_forever):
        await supervisor.reconcile_instance(1)

    assert 1 in supervisor._tasks  # noqa: SLF001
    await supervisor.stop()


@pytest.mark.asyncio()
async def test_reconcile_stops_for_disabled(seeded_instances: None) -> None:
    """reconcile_instance stops the task when instance is disabled."""
    supervisor = Supervisor(master_key=MASTER_KEY)

    async def _wait_forever(self: object, iid: int, startup_offset: int = 0) -> None:
        await asyncio.Event().wait()

    with patch.object(Supervisor, "_instance_loop", _wait_forever):
        await supervisor.start_instance_task(1)

    async with get_db() as conn:
        await conn.execute("UPDATE instances SET enabled = 0 WHERE id = 1")
        await conn.commit()

    await supervisor.reconcile_instance(1)
    assert 1 not in supervisor._tasks  # noqa: SLF001


# ---------------------------------------------------------------------------
# Connection error dedup and recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_connection_error_first_failure_logs_error(seeded_instances: None) -> None:
    """First TransportError writes one error log row."""
    instance = _make_instance()
    supervisor = Supervisor(master_key=MASTER_KEY)

    call_count = 0

    async def _run_search_side_effect(inst: Instance, mk: bytes, **kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TransportError("Connection refused")
        return 0

    async def _sleep_then_cancel(secs: float) -> None:
        if secs == _supervisor_mod._CONNECT_RETRY_SECS:  # noqa: SLF001
            raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "run_instance_search", side_effect=_run_search_side_effect),
        patch.object(_supervisor_mod, "get_instance", return_value=instance),
        patch("asyncio.sleep", side_effect=_sleep_then_cancel),
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor._instance_loop(1, startup_offset=0)  # noqa: SLF001

    rows = await _get_log_rows()
    error_rows = [r for r in rows if r["action"] == "error"]
    assert len(error_rows) == 1
    assert "Could not reach" in (error_rows[0]["message"] or "")


@pytest.mark.asyncio()
async def test_connection_recovery_logs_info(seeded_instances: None) -> None:
    """After error then success, a recovery info row is logged."""
    instance = _make_instance()
    supervisor = Supervisor(master_key=MASTER_KEY)

    call_count = 0

    async def _run_search_side_effect(inst: Instance, mk: bytes, **kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TransportError("Connection refused")
        return 0

    cycle = 0

    async def _controlled_sleep(secs: float) -> None:
        nonlocal cycle
        cycle += 1
        if cycle >= 3:
            raise asyncio.CancelledError

    with (
        patch.object(_supervisor_mod, "run_instance_search", side_effect=_run_search_side_effect),
        patch.object(_supervisor_mod, "get_instance", return_value=instance),
        patch("asyncio.sleep", side_effect=_controlled_sleep),
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor._instance_loop(1, startup_offset=0)  # noqa: SLF001

    rows = await _get_log_rows()
    info_rows = [r for r in rows if r["action"] == "info" and r.get("message")]
    recovery = [r for r in info_rows if "reachable again" in (r["message"] or "")]
    assert len(recovery) == 1


# ---------------------------------------------------------------------------
# Staggered startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_staggered_startup_uses_offset(seeded_instances: None) -> None:
    """start() uses _STARTUP_STAGGER_SECS as offset between instances."""
    # Insert a second instance
    from houndarr.crypto import encrypt

    encrypted = encrypt("test-api-key", MASTER_KEY)
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            (2, "Test Sonarr 2", "sonarr", SONARR_URL, encrypted),
        )
        await conn.commit()

    offsets: list[int] = []

    async def _capture_offset(
        self: Supervisor, iid: int, *, instance: Instance | None = None, startup_offset: int = 0
    ) -> bool:
        offsets.append(startup_offset)
        return True

    supervisor = Supervisor(master_key=MASTER_KEY)
    with patch.object(Supervisor, "start_instance_task", _capture_offset):
        await supervisor.start()

    assert offsets == [0, 30]


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_graceful_shutdown_cancels_tasks(seeded_instances: None) -> None:
    """stop() cancels all running tasks."""
    supervisor = Supervisor(master_key=MASTER_KEY)

    async def _wait_forever(self: object, iid: int, startup_offset: int = 0) -> None:
        await asyncio.Event().wait()

    with patch.object(Supervisor, "_instance_loop", _wait_forever):
        await supervisor.start_instance_task(1)

    assert 1 in supervisor._tasks  # noqa: SLF001
    await supervisor.stop()
    assert len(supervisor._tasks) == 0  # noqa: SLF001


@pytest.mark.asyncio()
async def test_instance_deleted_exits_loop(seeded_instances: None) -> None:
    """Loop exits when get_instance returns None (instance deleted)."""
    supervisor = Supervisor(master_key=MASTER_KEY)

    async def _controlled_sleep(secs: float) -> None:
        pass

    with (
        patch.object(_supervisor_mod, "get_instance", return_value=None),
        patch("asyncio.sleep", side_effect=_controlled_sleep),
    ):
        await supervisor._instance_loop(1, startup_offset=0)  # noqa: SLF001

    # Loop should have exited without error


@pytest.mark.asyncio()
async def test_instance_disabled_exits_loop(seeded_instances: None) -> None:
    """Loop exits when instance is disabled."""
    instance = _make_instance(enabled=False)
    supervisor = Supervisor(master_key=MASTER_KEY)

    async def _controlled_sleep(secs: float) -> None:
        pass

    with (
        patch.object(_supervisor_mod, "get_instance", return_value=instance),
        patch("asyncio.sleep", side_effect=_controlled_sleep),
    ):
        await supervisor._instance_loop(1, startup_offset=0)  # noqa: SLF001


@pytest.mark.asyncio()
async def test_normal_cycle_sleeps_instance_interval(seeded_instances: None) -> None:
    """After a successful cycle, sleeps for sleep_interval_mins * 60."""
    instance = _make_instance(sleep_interval_mins=5)
    supervisor = Supervisor(master_key=MASTER_KEY)

    sleep_values: list[float] = []
    call_count = 0

    async def _capture_sleep(secs: float) -> None:
        nonlocal call_count
        sleep_values.append(secs)
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    with (
        patch.object(
            _supervisor_mod,
            "run_instance_search",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch.object(_supervisor_mod, "get_instance", return_value=instance),
        patch("asyncio.sleep", side_effect=_capture_sleep),
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor._instance_loop(1, startup_offset=0)  # noqa: SLF001

    # First sleep is startup grace (10 + 0), second is interval (5 * 60 = 300)
    assert len(sleep_values) >= 2
    assert sleep_values[0] == 10  # _STARTUP_GRACE_SECS + offset(0)
    assert sleep_values[1] == 300  # 5 * 60


@pytest.mark.asyncio()
async def test_connection_retry_sleeps_30s(seeded_instances: None) -> None:
    """After a connection error, sleeps _CONNECT_RETRY_SECS (30s)."""
    instance = _make_instance()
    supervisor = Supervisor(master_key=MASTER_KEY)

    sleep_values: list[float] = []
    call_count = 0

    async def _capture_sleep(secs: float) -> None:
        nonlocal call_count
        sleep_values.append(secs)
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    async def _always_error(inst: Instance, mk: bytes, **kwargs: object) -> int:
        raise httpx.TransportError("Connection refused")

    with (
        patch.object(_supervisor_mod, "run_instance_search", side_effect=_always_error),
        patch.object(_supervisor_mod, "get_instance", return_value=instance),
        patch("asyncio.sleep", side_effect=_capture_sleep),
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor._instance_loop(1, startup_offset=0)  # noqa: SLF001

    # First sleep is startup grace, second is retry interval
    assert len(sleep_values) >= 2
    assert sleep_values[1] == 30  # _CONNECT_RETRY_SECS


@pytest.mark.asyncio()
async def test_no_enabled_instances_logs_warning(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    """start() with no enabled instances logs a warning."""
    async with get_db() as conn:
        await conn.execute("UPDATE instances SET enabled = 0 WHERE id = 1")
        await conn.commit()

    supervisor = Supervisor(master_key=MASTER_KEY)
    with caplog.at_level("WARNING"):
        await supervisor.start()

    assert any("no enabled instances" in r.message.lower() for r in caplog.records)

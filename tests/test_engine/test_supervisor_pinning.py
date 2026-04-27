"""Pin the Supervisor public-API lifecycle contract.

Locks idempotency, return values, and task-bookkeeping invariants
on ``start``, ``stop``, ``start_instance_task``,
``stop_instance_task``, ``reconcile_instance``, and
``trigger_run_now`` so later edits to the supervisor cannot drift
any of them.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine import supervisor as supervisor_module
from houndarr.engine.supervisor import Supervisor

pytestmark = pytest.mark.pinning


_MASTER_KEY = Fernet.generate_key()


def _encrypt_key(value: str) -> str:
    """Return the Fernet token as a str so decrypt() can .encode() it."""
    return Fernet(_MASTER_KEY).encrypt(value.encode()).decode()


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed one enabled Sonarr instance and one disabled Radarr."""
    enc = _encrypt_key("test-api-key")
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO instances
            (id, name, type, url, encrypted_api_key, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Sonarr", "sonarr", "http://sonarr:8989", enc, 1),
                (2, "Radarr", "radarr", "http://radarr:7878", enc, 0),
            ],
        )
        await conn.commit()
    yield


@pytest_asyncio.fixture()
async def supervisor(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[Supervisor, None]:
    """Return a supervisor whose lifecycle is cleanly torn down."""
    # Disarm the real search loop so ``start_instance_task`` never hits *arr.
    monkeypatch.setattr(supervisor_module, "run_instance_search", AsyncMock(return_value=0))
    # Skip the startup grace + refresh loop waits so tests finish quickly.
    monkeypatch.setattr(supervisor_module, "_STARTUP_GRACE_SECS", 0)
    monkeypatch.setattr(supervisor_module, "_STARTUP_STAGGER_SECS", 0)

    sup = Supervisor(master_key=_MASTER_KEY)
    yield sup
    await sup.stop()


# trigger_run_now


class TestTriggerRunNow:
    @pytest.mark.asyncio()
    async def test_unknown_instance_returns_not_found(self, supervisor: Supervisor) -> None:
        assert await supervisor.trigger_run_now(9999) == "not_found"

    @pytest.mark.asyncio()
    async def test_disabled_instance_returns_disabled(self, supervisor: Supervisor) -> None:
        assert await supervisor.trigger_run_now(2) == "disabled"

    @pytest.mark.asyncio()
    async def test_enabled_instance_returns_accepted(self, supervisor: Supervisor) -> None:
        assert await supervisor.trigger_run_now(1) == "accepted"

    @pytest.mark.asyncio()
    async def test_second_concurrent_call_also_accepted(self, supervisor: Supervisor) -> None:
        """An in-flight manual run coalesces: both callers see 'accepted'."""
        first = await supervisor.trigger_run_now(1)
        second = await supervisor.trigger_run_now(1)
        assert first == "accepted"
        assert second == "accepted"


# start_instance_task / stop_instance_task / reconcile_instance


class TestInstanceTaskLifecycle:
    @pytest.mark.asyncio()
    async def test_start_instance_task_is_idempotent(self, supervisor: Supervisor) -> None:
        """Calling start twice on the same instance spawns at most one task."""
        await supervisor.start_instance_task(1)
        task_1 = supervisor._tasks.get(1)
        await supervisor.start_instance_task(1)
        task_2 = supervisor._tasks.get(1)
        assert task_1 is task_2

    @pytest.mark.asyncio()
    async def test_stop_instance_task_returns_true_when_present(
        self, supervisor: Supervisor
    ) -> None:
        await supervisor.start_instance_task(1)
        assert await supervisor.stop_instance_task(1) is True
        assert 1 not in supervisor._tasks

    @pytest.mark.asyncio()
    async def test_stop_instance_task_returns_false_when_missing(
        self, supervisor: Supervisor
    ) -> None:
        assert await supervisor.stop_instance_task(9999) is False

    @pytest.mark.asyncio()
    async def test_reconcile_starts_enabled_instance(self, supervisor: Supervisor) -> None:
        await supervisor.reconcile_instance(1)
        assert 1 in supervisor._tasks

    @pytest.mark.asyncio()
    async def test_reconcile_does_not_start_disabled(self, supervisor: Supervisor) -> None:
        await supervisor.reconcile_instance(2)
        assert 2 not in supervisor._tasks

    @pytest.mark.asyncio()
    async def test_reconcile_stops_newly_disabled(
        self,
        supervisor: Supervisor,
    ) -> None:
        """Toggle an enabled instance off: reconcile stops the task."""
        await supervisor.start_instance_task(1)
        async with get_db() as conn:
            await conn.execute("UPDATE instances SET enabled = 0 WHERE id = 1")
            await conn.commit()
        await supervisor.reconcile_instance(1)
        assert 1 not in supervisor._tasks

    @pytest.mark.asyncio()
    async def test_reconcile_noop_for_missing_instance(
        self,
        supervisor: Supervisor,
    ) -> None:
        """Deleted instance: reconcile stops any dangling task without raising."""
        await supervisor.reconcile_instance(9999)
        assert 9999 not in supervisor._tasks


# Module-level constants (pinning drift protection)


class TestModuleConstants:
    def test_connect_retry_is_30_seconds(self) -> None:
        assert supervisor_module._CONNECT_RETRY_SECS == 30

    def test_snapshot_refresh_interval_is_10_minutes(self) -> None:
        assert supervisor_module._SNAPSHOT_REFRESH_INTERVAL_SECS == 600

    def test_shutdown_timeout_is_10_seconds(self) -> None:
        assert supervisor_module._SHUTDOWN_TIMEOUT == 10

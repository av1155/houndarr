"""Tests for the Supervisor engine - connection-error deduplication and startup grace."""

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
# Tests - startup grace delay
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
            await supervisor._instance_loop(instance.core.id)  # noqa: SLF001

    # The very first sleep must be the startup grace, not the inter-cycle sleep.
    assert sleep_calls, "expected at least one asyncio.sleep call"
    assert sleep_calls[0] == _STARTUP_GRACE_SECS
    # No search should have run because CancelledError fires in the first sleep.
    mock_search.assert_not_called()


@pytest.mark.asyncio()
async def test_instance_loop_applies_startup_offset(
    seeded_instances: None,
) -> None:
    """startup_offset is added to _STARTUP_GRACE_SECS for the first sleep."""
    instance = _make_instance()
    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
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
            await supervisor._instance_loop(instance.core.id, startup_offset=60)  # noqa: SLF001

    assert sleep_calls, "expected at least one asyncio.sleep call"
    assert sleep_calls[0] == 70  # 10 (grace) + 60 (offset)
    mock_search.assert_not_called()


@pytest.mark.asyncio()
async def test_start_staggers_instance_tasks() -> None:
    """start() passes idx * _STARTUP_STAGGER_SECS as startup_offset to each task."""
    from houndarr.engine.supervisor import _STARTUP_STAGGER_SECS

    instance1 = _make_instance(instance_id=1)
    instance2 = _make_instance(instance_id=2, url="http://radarr:7878")

    with (
        patch.object(_supervisor_mod, "_STARTUP_STAGGER_SECS", 30),
        patch("houndarr.engine.supervisor.list_instances", return_value=[instance1, instance2]),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        mock_start = AsyncMock(return_value=True)
        supervisor.start_instance_task = mock_start  # noqa: SLF001
        await supervisor.start()

    assert mock_start.call_count == 2
    assert mock_start.call_args_list[0].args == (instance1.core.id,)
    assert mock_start.call_args_list[0].kwargs["startup_offset"] == 0
    assert mock_start.call_args_list[1].args == (instance2.core.id,)
    assert mock_start.call_args_list[1].kwargs["startup_offset"] == _STARTUP_STAGGER_SECS


# ---------------------------------------------------------------------------
# Tests - state-transition error logging
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
            await supervisor._instance_loop(instance.core.id)  # noqa: SLF001

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
            await supervisor._instance_loop(instance.core.id)  # noqa: SLF001

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
            return 0  # success - triggers recovery path
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
            await supervisor._instance_loop(instance.core.id)  # noqa: SLF001

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
            await supervisor._instance_loop(instance.core.id)  # noqa: SLF001

    rows = await _get_log_rows()
    # Expect exactly: 1 error (first failure) + 1 info (recovery) = 2 rows total
    assert len(rows) == 2, f"expected 2 log rows (1 error + 1 recovery), got: {rows}"
    assert rows[0]["action"] == "error"
    assert rows[1]["action"] == "info"


# ---------------------------------------------------------------------------
# Snapshot refresh loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_refresh_all_snapshots_once_updates_enabled_instances(
    seeded_instances: None,
) -> None:
    """_refresh_all_snapshots_once calls update_instance_snapshot on each
    enabled instance using the snapshot composed by the adapter.
    """
    from houndarr.clients.base import InstanceSnapshot, ReconcileSets
    from houndarr.services.instances import get_instance

    inst = _make_instance(enabled=True)

    fake_client = AsyncMock()

    class _CtxClient:
        async def __aenter__(self) -> Any:
            return fake_client

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    fake_adapter = type(
        "FakeAdapter",
        (),
        {
            "make_client": staticmethod(lambda _inst: _CtxClient()),
            "fetch_instance_snapshot": staticmethod(
                AsyncMock(return_value=InstanceSnapshot(monitored_total=42, unreleased_count=3))
            ),
            "fetch_reconcile_sets": staticmethod(AsyncMock(return_value=ReconcileSets.empty())),
        },
    )()

    with (
        patch("houndarr.engine.supervisor.list_instances", AsyncMock(return_value=[inst])),
        patch("houndarr.engine.supervisor.get_adapter", return_value=fake_adapter),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        await supervisor._refresh_all_snapshots_once()  # noqa: SLF001

    refreshed = await get_instance(inst.core.id, master_key=MASTER_KEY)
    assert refreshed is not None
    assert refreshed.snapshot.monitored_total == 42
    assert refreshed.snapshot.unreleased_count == 3
    assert refreshed.snapshot.snapshot_refreshed_at != ""


@pytest.mark.asyncio()
async def test_refresh_all_snapshots_skips_disabled(
    seeded_instances: None,
) -> None:
    """Disabled instances are not probed and retain prior snapshot values."""
    from houndarr.services.instances import get_instance

    inst = _make_instance(enabled=False)
    fake_client_call = AsyncMock()

    with (
        patch("houndarr.engine.supervisor.list_instances", AsyncMock(return_value=[inst])),
        patch("houndarr.engine.supervisor.get_adapter", side_effect=fake_client_call),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        await supervisor._refresh_all_snapshots_once()  # noqa: SLF001

    assert fake_client_call.await_count == 0
    refreshed = await get_instance(inst.core.id, master_key=MASTER_KEY)
    assert refreshed is not None
    assert refreshed.snapshot.monitored_total == 0  # unchanged from default


@pytest.mark.asyncio()
async def test_refresh_one_snapshot_logs_big_unreleased_jump(
    seeded_instances: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A jump above the threshold emits a single INFO line.

    Smoke test for the post-upgrade observability path: the prior
    DB value seeded at 0, the snapshot returns 16, the delta crosses
    the threshold (>10), so the log must fire once.
    """
    from houndarr.clients.base import InstanceSnapshot, ReconcileSets

    inst = _make_instance(enabled=True)

    fake_client = AsyncMock()

    class _CtxClient:
        async def __aenter__(self) -> Any:
            return fake_client

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    fake_adapter = type(
        "FakeAdapter",
        (),
        {
            "make_client": staticmethod(lambda _inst: _CtxClient()),
            "fetch_instance_snapshot": staticmethod(
                AsyncMock(return_value=InstanceSnapshot(monitored_total=20, unreleased_count=16))
            ),
            "fetch_reconcile_sets": staticmethod(AsyncMock(return_value=ReconcileSets.empty())),
        },
    )()

    with (
        caplog.at_level("INFO", logger="houndarr.engine.supervisor"),
        patch("houndarr.engine.supervisor.get_adapter", return_value=fake_adapter),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        await supervisor._refresh_one_snapshot(inst)  # noqa: SLF001

    matching = [r for r in caplog.records if "unreleased jumped" in r.getMessage()]
    assert len(matching) == 1
    assert "0 -> 16" in matching[0].getMessage()


@pytest.mark.asyncio()
async def test_refresh_one_snapshot_quiet_on_small_unreleased_change(
    seeded_instances: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A jump within the threshold stays silent.

    Threshold is intentionally noisy enough to ignore single-release
    churn (one item flipping past midnight); the test pins the
    "no log fired" half of the contract.
    """
    from houndarr.clients.base import InstanceSnapshot, ReconcileSets
    from houndarr.repositories.instances import update_instance_snapshot

    inst = _make_instance(enabled=True)

    # Seed a prior count of 5; the new snapshot returns 7 (delta = 2).
    await update_instance_snapshot(inst.core.id, monitored_total=10, unreleased_count=5)

    fake_client = AsyncMock()

    class _CtxClient:
        async def __aenter__(self) -> Any:
            return fake_client

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    fake_adapter = type(
        "FakeAdapter",
        (),
        {
            "make_client": staticmethod(lambda _inst: _CtxClient()),
            "fetch_instance_snapshot": staticmethod(
                AsyncMock(return_value=InstanceSnapshot(monitored_total=10, unreleased_count=7))
            ),
            "fetch_reconcile_sets": staticmethod(AsyncMock(return_value=ReconcileSets.empty())),
        },
    )()

    with (
        caplog.at_level("INFO", logger="houndarr.engine.supervisor"),
        patch("houndarr.engine.supervisor.get_adapter", return_value=fake_adapter),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        await supervisor._refresh_one_snapshot(inst)  # noqa: SLF001

    matching = [r for r in caplog.records if "unreleased jumped" in r.getMessage()]
    assert matching == []


@pytest.mark.asyncio()
async def test_refresh_all_snapshots_handles_transport_error(
    seeded_instances: None,
) -> None:
    """Transport errors are logged and suppressed; snapshot stays as-is."""
    inst = _make_instance(enabled=True)

    class _BrokenCtx:
        async def __aenter__(self) -> Any:
            raise httpx.TransportError("unreachable")

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    fake_adapter = type(
        "FakeAdapter",
        (),
        {"make_client": staticmethod(lambda _inst: _BrokenCtx())},
    )()

    with (
        patch("houndarr.engine.supervisor.list_instances", AsyncMock(return_value=[inst])),
        patch("houndarr.engine.supervisor.get_adapter", return_value=fake_adapter),
    ):
        supervisor = Supervisor(master_key=MASTER_KEY)
        # Should not raise.
        await supervisor._refresh_all_snapshots_once()  # noqa: SLF001

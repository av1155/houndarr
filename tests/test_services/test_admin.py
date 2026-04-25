"""Unit tests for houndarr.services.admin."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from houndarr.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_BATCH_SIZE,
    DEFAULT_CUTOFF_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_HOURLY_CAP,
    DEFAULT_HOURLY_CAP,
    DEFAULT_POST_RELEASE_GRACE_HOURS,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_UPGRADE_BATCH_SIZE,
    DEFAULT_UPGRADE_COOLDOWN_DAYS,
    DEFAULT_UPGRADE_HOURLY_CAP,
)
from houndarr.database import get_db, set_db_path
from houndarr.services.admin import (
    clear_all_search_logs,
    factory_reset,
    reset_all_instance_policy,
)
from houndarr.services.instances import InstanceType, create_instance, get_instance

_MASTER_KEY = Fernet.generate_key()


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[list[int], None]:
    """Create two instances with non-default policy so a reset has something to prove."""
    inst1 = await create_instance(
        master_key=_MASTER_KEY,
        name="Sonarr 4K",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="sonarr-key",
        batch_size=25,
        sleep_interval_mins=5,
        hourly_cap=99,
        cooldown_days=42,
        post_release_grace_hrs=48,
        queue_limit=10,
        cutoff_enabled=True,
        cutoff_batch_size=7,
        cutoff_cooldown_days=90,
        cutoff_hourly_cap=5,
        upgrade_enabled=True,
        upgrade_batch_size=3,
        upgrade_cooldown_days=60,
        upgrade_hourly_cap=2,
        allowed_time_window="09:00-17:00",
    )
    inst2 = await create_instance(
        master_key=_MASTER_KEY,
        name="Radarr Movies",
        type=InstanceType.radarr,
        url="http://radarr:7878",
        api_key="radarr-key",
        batch_size=50,
        hourly_cap=20,
    )
    yield [inst1.core.id, inst2.core.id]


# ---------------------------------------------------------------------------
# reset_all_instance_policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_returns_count(seeded_instances: list[int]) -> None:
    count = await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)
    assert count == 2


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_reverts_columns(
    seeded_instances: list[int],
) -> None:
    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)
    inst = await get_instance(seeded_instances[0], master_key=_MASTER_KEY)
    assert inst is not None
    assert inst.missing.batch_size == DEFAULT_BATCH_SIZE
    assert inst.missing.sleep_interval_mins == DEFAULT_SLEEP_INTERVAL_MINUTES
    assert inst.missing.hourly_cap == DEFAULT_HOURLY_CAP
    assert inst.missing.cooldown_days == DEFAULT_COOLDOWN_DAYS
    assert inst.missing.post_release_grace_hrs == DEFAULT_POST_RELEASE_GRACE_HOURS
    assert inst.missing.queue_limit == DEFAULT_QUEUE_LIMIT
    assert inst.cutoff.cutoff_enabled is False
    assert inst.cutoff.cutoff_batch_size == DEFAULT_CUTOFF_BATCH_SIZE
    assert inst.cutoff.cutoff_cooldown_days == DEFAULT_CUTOFF_COOLDOWN_DAYS
    assert inst.cutoff.cutoff_hourly_cap == DEFAULT_CUTOFF_HOURLY_CAP
    assert inst.upgrade.upgrade_enabled is False
    assert inst.upgrade.upgrade_batch_size == DEFAULT_UPGRADE_BATCH_SIZE
    assert inst.upgrade.upgrade_cooldown_days == DEFAULT_UPGRADE_COOLDOWN_DAYS
    assert inst.upgrade.upgrade_hourly_cap == DEFAULT_UPGRADE_HOURLY_CAP
    assert inst.schedule.allowed_time_window == ""
    assert inst.schedule.missing_page_offset == 1
    assert inst.schedule.cutoff_page_offset == 1
    assert inst.upgrade.upgrade_item_offset == 0
    assert inst.upgrade.upgrade_series_offset == 0


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_preserves_identity(
    seeded_instances: list[int],
) -> None:
    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)
    inst = await get_instance(seeded_instances[0], master_key=_MASTER_KEY)
    assert inst is not None
    assert inst.core.name == "Sonarr 4K"
    assert inst.core.type is InstanceType.sonarr
    assert inst.core.url == "http://sonarr:8989"
    assert inst.core.api_key == "sonarr-key"  # Decrypted round-trip still works
    assert inst.core.enabled is True


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_calls_reconcile_per_row(
    seeded_instances: list[int],
) -> None:
    supervisor = AsyncMock()
    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=supervisor)
    assert supervisor.reconcile_instance.await_count == 2
    called_ids = {call.args[0] for call in supervisor.reconcile_instance.await_args_list}
    assert called_ids == set(seeded_instances)


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_writes_audit_row(
    seeded_instances: list[int],
) -> None:
    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)
    async with get_db() as conn:
        async with conn.execute(
            "SELECT message, action, cycle_trigger FROM search_log"
            " WHERE action = 'info' AND cycle_trigger = 'system'"
            " ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert "Policy settings reset" in row["message"]


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_no_instances(db: None) -> None:
    count = await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)
    assert count == 0


# ---------------------------------------------------------------------------
# clear_all_search_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_clear_all_search_logs_empties_table(db: None) -> None:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO search_log (action, message) VALUES (?, ?)",
            [
                ("searched", "item 1"),
                ("skipped", "item 2"),
                ("error", "boom"),
            ],
        )
        await conn.commit()

    removed = await clear_all_search_logs()
    assert removed == 3

    # Exactly one audit row remains (the breadcrumb written after the delete).
    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) AS n FROM search_log") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["n"] == 1


@pytest.mark.asyncio()
async def test_clear_all_search_logs_records_breadcrumb(db: None) -> None:
    async with get_db() as conn:
        await conn.execute("INSERT INTO search_log (action, message) VALUES ('info', 'seed')")
        await conn.commit()

    await clear_all_search_logs()

    async with get_db() as conn:
        async with conn.execute(
            "SELECT message, action, cycle_trigger FROM search_log ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["action"] == "info"
    assert row["cycle_trigger"] == "system"
    assert "Audit log cleared" in row["message"]


@pytest.mark.asyncio()
async def test_clear_all_search_logs_empty_table(db: None) -> None:
    removed = await clear_all_search_logs()
    assert removed == 0
    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) AS n FROM search_log") as cur:
            row = await cur.fetchone()
    # The breadcrumb is inserted even when nothing was removed, so exactly 1.
    assert row is not None
    assert row["n"] == 1


# ---------------------------------------------------------------------------
# factory_reset
# ---------------------------------------------------------------------------


class _FakeSupervisor:
    """Minimal stand-in for Supervisor so isinstance checks pass in tests."""

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True

    async def start(self) -> None:  # pragma: no cover - not driven in unit tests
        pass


class _FakeApp:
    def __init__(self, supervisor: object | None, master_key: bytes) -> None:
        self.state = type("State", (), {})()
        self.state.supervisor = supervisor
        self.state.master_key = master_key


@pytest.mark.asyncio()
async def test_factory_reset_deletes_files_and_reinits(
    tmp_data_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a DB + master-key file so there is something to wipe.
    db_path = os.path.join(tmp_data_dir, "houndarr.db")
    key_path = os.path.join(tmp_data_dir, "houndarr.masterkey")
    set_db_path(db_path)
    from houndarr.database import init_db

    await init_db()
    original_key = Fernet.generate_key()
    Path(key_path).write_bytes(original_key)

    fake_app = _FakeApp(supervisor=None, master_key=original_key)

    # Skip the real Supervisor.start() so no asyncio tasks leak; we only
    # care that factory_reset rewires app.state with a Supervisor instance.
    monkeypatch.setattr(
        "houndarr.engine.supervisor.Supervisor.start",
        AsyncMock(return_value=None),
    )

    await factory_reset(app=fake_app, data_dir=tmp_data_dir)

    # DB file recreated, master-key file rotated.
    assert Path(db_path).exists()
    new_key = Path(key_path).read_bytes()
    assert new_key != original_key

    # app.state rewired to the fresh state.
    assert fake_app.state.master_key == new_key
    assert fake_app.state.supervisor is not None

    # Auth caches reset.
    import houndarr.auth as _auth

    assert _auth._setup_complete is None  # noqa: SLF001
    assert _auth._serializer is None  # noqa: SLF001
    assert _auth._login_attempts == {}  # noqa: SLF001


@pytest.mark.asyncio()
async def test_factory_reset_propagates_reinit_failure(
    tmp_data_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the in-process re-init raises, the exception surfaces as a typed
    :class:`~houndarr.errors.ServiceError` with the original on
    ``__cause__``.  The route layer's hybrid fallback then schedules
    a process exit so the orchestrator restarts the container; on
    next boot the empty data dir drops into first-run.  No sentinel
    file is written: the container restart is the recovery
    mechanism.
    """
    from houndarr.errors import ServiceError

    db_path = os.path.join(tmp_data_dir, "houndarr.db")
    set_db_path(db_path)
    from houndarr.database import init_db as _real_init_db

    await _real_init_db()
    Path(tmp_data_dir, "houndarr.masterkey").write_bytes(Fernet.generate_key())
    fake_app = _FakeApp(supervisor=None, master_key=b"x")

    async def _boom() -> None:
        msg = "init_db blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr("houndarr.services.admin.init_db", _boom)

    with pytest.raises(ServiceError) as exc_info:
        await factory_reset(app=fake_app, data_dir=tmp_data_dir)

    # Typed wrap: the original RuntimeError lives on __cause__ so the
    # observability hook still sees the underlying shape.
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "init_db blew up" in str(exc_info.value.__cause__)

    # The on-disk DB and masterkey are already wiped (the file-delete
    # step ran before init_db), so a container restart lands on first-run.
    assert not Path(db_path).exists()
    assert not Path(tmp_data_dir, "houndarr.masterkey").exists()

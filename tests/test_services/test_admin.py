"""Unit tests for houndarr.services.admin."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI

from houndarr.config import DEFAULT_UPGRADE_SERIES_WINDOW_SIZE
from houndarr.database import close_all_pools, get_db, set_db_path
from houndarr.errors import ServiceError
from houndarr.routes.settings._helpers import blank_instance
from houndarr.services.admin import (
    _policy_defaults,
    clear_all_search_logs,
    factory_reset,
    reset_all_instance_policy,
)
from houndarr.services.instances import (
    CutoffPolicy,
    InstanceType,
    MissingPolicy,
    SchedulePolicy,
    SearchOrder,
    SonarrSearchMode,
    TagFilterPolicy,
    UpgradePolicy,
    create_instance,
    get_instance,
    update_instance,
    update_instance_snapshot,
)

_MASTER_KEY = Fernet.generate_key()

# Columns a policy reset leaves out: identity, timestamps, snapshot counters and tag filters.
_RESET_KEEPS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "type",
        "url",
        "encrypted_api_key",
        "enabled",
        "created_at",
        "updated_at",
        "monitored_total",
        "unreleased_count",
        "snapshot_refreshed_at",
        "tag_filter_include",
        "tag_filter_exclude",
        "tag_filter_missing_exclude",
        "tag_filter_cutoff_exclude",
        "tag_filter_upgrade_exclude",
    }
)


# A factory_reset test opens its pools without the ``db`` fixture; an open
# pool's aiosqlite threads are non-daemon and keep the interpreter from exiting.
@pytest_asyncio.fixture(autouse=True)
async def _close_pools() -> AsyncGenerator[None]:
    yield
    await close_all_pools()


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[list[int]]:
    """Create two instances with non-default policy so a reset has something to prove."""
    inst1 = await create_instance(
        master_key=_MASTER_KEY,
        name="Sonarr 4K",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="sonarr-key",
        missing_enabled=False,
        batch_size=25,
        sleep_interval_mins=5,
        hourly_cap=99,
        cooldown_days=42,
        post_release_grace_hrs=48,
        missing_hot_retry_window_hrs=24,
        missing_hot_retry_interval_hrs=4,
        queue_limit=10,
        cutoff_enabled=True,
        cutoff_batch_size=7,
        cutoff_cooldown_days=90,
        cutoff_hourly_cap=5,
        sonarr_search_mode=SonarrSearchMode.season_context,
        upgrade_enabled=True,
        upgrade_batch_size=3,
        upgrade_cooldown_days=60,
        upgrade_hourly_cap=2,
        upgrade_sonarr_search_mode=SonarrSearchMode.season_context,
        upgrade_series_window_size=20,
        allowed_time_window="09:00-17:00",
        search_order=SearchOrder.chronological,
    )
    # Cursors and snapshot counters have no create_instance kwargs.
    await update_instance(
        inst1.core.id,
        master_key=_MASTER_KEY,
        missing_page_offset=7,
        cutoff_page_offset=3,
        upgrade_item_offset=11,
        upgrade_series_offset=4,
    )
    await update_instance_snapshot(inst1.core.id, monitored_total=120, unreleased_count=8)
    inst2 = await create_instance(
        master_key=_MASTER_KEY,
        name="Radarr Movies",
        type=InstanceType.radarr,
        url="http://radarr:7878",
        api_key="radarr-key",
        enabled=False,
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
    for instance_id in seeded_instances:
        inst = await get_instance(instance_id, master_key=_MASTER_KEY)
        assert inst is not None
        assert inst.missing == MissingPolicy()
        assert inst.cutoff == CutoffPolicy()
        assert inst.upgrade == UpgradePolicy()
        assert inst.schedule == SchedulePolicy()


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_reenables_missing_and_resets_series_window(
    db: None,
) -> None:
    created = await create_instance(
        master_key=_MASTER_KEY,
        name="Sonarr Paused",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="sonarr-key",
        missing_enabled=False,
        upgrade_series_window_size=20,
    )

    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)

    inst = await get_instance(created.core.id, master_key=_MASTER_KEY)
    assert inst is not None
    assert inst.missing.missing_enabled is True
    assert inst.upgrade.upgrade_series_window_size == DEFAULT_UPGRADE_SERIES_WINDOW_SIZE


@pytest.mark.asyncio()
async def test_policy_defaults_cover_every_policy_column(db: None) -> None:
    """A new instances column must join the reset or the set of columns it keeps."""
    async with get_db() as conn:
        async with conn.execute("PRAGMA table_info(instances)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
    assert columns >= _RESET_KEEPS
    assert columns - _RESET_KEEPS == set(_policy_defaults())


def test_policy_defaults_match_instance_defaults() -> None:
    """The reset writes the policy defaults the Add form pre-fills for a new instance."""
    expected: dict[str, object] = {}
    for policy in (MissingPolicy(), CutoffPolicy(), UpgradePolicy(), SchedulePolicy()):
        expected.update(dataclasses.asdict(policy))
    blank = blank_instance()
    add_form: dict[str, object] = {}
    for policy in (blank.missing, blank.cutoff, blank.upgrade, blank.schedule):
        add_form.update(dataclasses.asdict(policy))
    assert add_form == expected
    assert _policy_defaults() == expected


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_leaves_tag_filters_alone(db: None) -> None:
    """Clearing a protective exclude on reset would widen what gets searched."""
    created = await create_instance(
        master_key=_MASTER_KEY,
        name="Sonarr Tags",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="sonarr-key",
        tag_filter_include="1080p",
        tag_filter_exclude="houndarr-skip",
        tag_filter_missing_exclude="no-backfill",
        tag_filter_cutoff_exclude="no-upgrade",
        tag_filter_upgrade_exclude="keep-sd",
    )

    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)

    inst = await get_instance(created.core.id, master_key=_MASTER_KEY)
    assert inst is not None
    assert inst.tag_filter == TagFilterPolicy(
        include=("1080p",),
        exclude=("houndarr-skip",),
        missing_exclude=("no-backfill",),
        cutoff_exclude=("no-upgrade",),
        upgrade_exclude=("keep-sd",),
    )


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_preserves_identity(
    seeded_instances: list[int],
) -> None:
    before = await get_instance(seeded_instances[0], master_key=_MASTER_KEY)
    assert before is not None
    await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)
    inst = await get_instance(seeded_instances[0], master_key=_MASTER_KEY)
    assert inst is not None
    assert inst.core.name == "Sonarr 4K"
    assert inst.core.type is InstanceType.sonarr
    assert inst.core.url == "http://sonarr:8989"
    assert inst.core.api_key == "sonarr-key"  # Decrypted round-trip still works
    assert inst.core.enabled is True
    assert inst.snapshot.monitored_total == 120
    assert inst.snapshot.unreleased_count == 8
    assert inst.snapshot.snapshot_refreshed_at != ""
    assert inst.snapshot == before.snapshot
    assert inst.timestamps.created_at == before.timestamps.created_at

    disabled = await get_instance(seeded_instances[1], master_key=_MASTER_KEY)
    assert disabled is not None
    assert disabled.core.enabled is False


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


async def _execute(sql: str) -> None:
    async with get_db() as conn:
        await conn.execute(sql)
        await conn.commit()


async def _system_log_count() -> int:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM search_log WHERE cycle_trigger = 'system'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    return int(row["n"])


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_changes_nothing_when_a_row_fails(
    seeded_instances: list[int],
) -> None:
    first, second = seeded_instances
    before = await get_instance(first, master_key=_MASTER_KEY)
    await _execute(
        f"CREATE TRIGGER fail_second BEFORE UPDATE ON instances WHEN OLD.id = {second}"
        " BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    supervisor = AsyncMock()

    with pytest.raises(ServiceError):
        await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=supervisor)

    assert await get_instance(first, master_key=_MASTER_KEY) == before
    assert await _system_log_count() == 0
    supervisor.reconcile_instance.assert_not_awaited()


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_changes_nothing_when_audit_row_fails(
    seeded_instances: list[int],
) -> None:
    before = [await get_instance(i, master_key=_MASTER_KEY) for i in seeded_instances]
    await _execute(
        "CREATE TRIGGER fail_audit BEFORE INSERT ON search_log"
        " WHEN NEW.cycle_trigger = 'system' BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )

    with pytest.raises(ServiceError):
        await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=None)

    assert [await get_instance(i, master_key=_MASTER_KEY) for i in seeded_instances] == before


@pytest.mark.asyncio()
async def test_reset_all_instance_policy_reconciles_every_row_when_one_reconcile_fails(
    seeded_instances: list[int],
) -> None:
    supervisor = AsyncMock()
    supervisor.reconcile_instance.side_effect = [RuntimeError("supervisor down"), None]

    count = await reset_all_instance_policy(master_key=_MASTER_KEY, supervisor=supervisor)

    assert count == 2
    assert supervisor.reconcile_instance.await_count == 2
    for instance_id in seeded_instances:
        inst = await get_instance(instance_id, master_key=_MASTER_KEY)
        assert inst is not None
        assert inst.missing == MissingPolicy()


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


@pytest.mark.asyncio()
async def test_clear_all_search_logs_keeps_rows_when_breadcrumb_fails(db: None) -> None:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO search_log (action, message) VALUES (?, ?)",
            [("searched", "item 1"), ("error", "boom")],
        )
        await conn.commit()
    await _execute(
        "CREATE TRIGGER fail_audit BEFORE INSERT ON search_log"
        " WHEN NEW.cycle_trigger = 'system' BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )

    with pytest.raises(ServiceError):
        await clear_all_search_logs()

    async with get_db() as conn:
        async with conn.execute("SELECT message FROM search_log ORDER BY id") as cur:
            rows = await cur.fetchall()
    assert [row["message"] for row in rows] == ["item 1", "boom"]


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
    """Test double for ``FastAPI`` covering only the ``state`` surface
    that :func:`houndarr.services.admin.factory_reset` reads / writes.

    ``state`` is typed ``Any`` so the dynamic attribute assignment and
    access used by ``factory_reset`` (``state.supervisor`` and
    ``state.master_key``) type-check without extra annotations.  The
    :func:`factory_reset` call sites in this module cast the test double
    to ``FastAPI`` since the helper takes a fully-typed ``FastAPI`` arg.
    """

    state: Any

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

    await factory_reset(app=cast("FastAPI", fake_app), data_dir=tmp_data_dir)

    # DB file recreated, master-key file rotated.
    assert Path(db_path).exists()
    new_key = Path(key_path).read_bytes()
    assert new_key != original_key

    # app.state rewired to the fresh state.
    assert fake_app.state.master_key == new_key
    assert fake_app.state.supervisor is not None

    # Auth caches reset.
    import houndarr.auth as _auth

    assert _auth._setup_complete is None
    assert _auth._serializer is None
    assert _auth._login_attempts == {}


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
        await factory_reset(app=cast("FastAPI", fake_app), data_dir=tmp_data_dir)

    # Typed wrap: the original RuntimeError lives on __cause__ so the
    # observability hook still sees the underlying shape.
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "init_db blew up" in str(exc_info.value.__cause__)

    # The on-disk DB and masterkey are already wiped (the file-delete
    # step ran before init_db), so a container restart lands on first-run.
    assert not Path(db_path).exists()
    assert not Path(tmp_data_dir, "houndarr.masterkey").exists()

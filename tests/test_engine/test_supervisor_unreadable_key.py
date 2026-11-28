"""The supervisor never searches with an API key it cannot decrypt (#934).

A lost or replaced ``houndarr.masterkey`` leaves every stored key encrypted
under the old one.  Such an instance gets no loop, and an enabled one shows
one error row until its key is entered again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from houndarr.crypto import encrypt
from houndarr.database import get_db
from houndarr.engine import supervisor as supervisor_module
from houndarr.engine.retry import CycleOutcome
from houndarr.engine.supervisor import Supervisor
from houndarr.services.instances import Instance, get_instance, update_instance

MASTER_KEY: bytes = Fernet.generate_key()
OTHER_KEY: bytes = Fernet.generate_key()
KEY_ERROR = "API key can't be decrypted with the current master key; enter it again in Settings"
READABLE, LOST, LOST_PAUSED = 1, 2, 3


@pytest_asyncio.fixture()
async def seeded(db: None) -> AsyncGenerator[None]:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key, enabled)"
            " VALUES (?, ?, 'sonarr', ?, ?, ?)",
            [
                (READABLE, "Readable", "http://s1:8989", encrypt("good", MASTER_KEY), 1),
                (LOST, "Lost", "http://s2:8989", encrypt("old", OTHER_KEY), 1),
                (LOST_PAUSED, "Lost paused", "http://s3:8989", encrypt("old", OTHER_KEY), 0),
            ],
        )
        await conn.commit()
    yield


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stand in for the loops and the snapshot prime; return the primed ids."""
    primed: list[int] = []

    async def idle_loop(self: Supervisor, instance_id: int, startup_offset: int = 0) -> None:
        await asyncio.Event().wait()

    async def record_prime(self: Supervisor, instance: Instance) -> None:
        primed.append(instance.core.id)

    monkeypatch.setattr(Supervisor, "_instance_loop", idle_loop)
    monkeypatch.setattr(Supervisor, "_refresh_one_snapshot", record_prime)
    return primed


async def _rows(instance_id: int) -> list[tuple[Any, ...]]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT action, message, cycle_trigger, cycle_id FROM search_log"
            " WHERE instance_id = ? ORDER BY id",
            (instance_id,),
        ) as cur:
            return [tuple(row) for row in await cur.fetchall()]


async def _flagged(instance_id: int) -> Instance:
    instance = await get_instance(instance_id, master_key=MASTER_KEY)
    assert instance is not None
    assert instance.core.api_key_unreadable
    return instance


async def test_start_runs_no_loop_for_a_key_it_cannot_decrypt(
    seeded: None, no_network: list[int]
) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    try:
        await supervisor.start()

        assert set(supervisor._tasks) == {READABLE}
        assert no_network == [READABLE]
        assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]
        assert await _rows(LOST_PAUSED) == []
        assert await _rows(READABLE) == []
    finally:
        await supervisor.stop()

    restarted = Supervisor(master_key=MASTER_KEY)
    try:
        await restarted.start()
        assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]
    finally:
        await restarted.stop()


async def test_an_unreadable_key_takes_no_startup_stagger_slot(
    seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
            " VALUES (4, 'Readable too', 'sonarr', 'http://s4:8989', ?)",
            (encrypt("good", MASTER_KEY),),
        )
        await conn.commit()
    offsets: dict[int, int] = {}

    async def record_offset(self: Supervisor, instance_id: int, startup_offset: int = 0) -> None:
        offsets[instance_id] = startup_offset

    async def no_prime(self: Supervisor, instance: Instance) -> None:
        return None

    monkeypatch.setattr(Supervisor, "_instance_loop", record_offset)
    monkeypatch.setattr(Supervisor, "_refresh_one_snapshot", no_prime)
    supervisor = Supervisor(master_key=MASTER_KEY)
    try:
        await supervisor.start()
        await asyncio.sleep(0)
    finally:
        await supervisor.stop()

    assert offsets == {READABLE: 0, 4: supervisor_module._STARTUP_STAGGER_SECS}


async def test_start_writes_the_error_again_once_a_newer_row_covers_it(
    seeded: None, no_network: list[int]
) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    try:
        await supervisor.start()
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO search_log (instance_id, action, message) VALUES (?, 'info', 'x')",
                (LOST,),
            )
            await conn.commit()

        await supervisor.reconcile_instance(LOST)

        assert [row[:2] for row in await _rows(LOST)] == [
            ("error", KEY_ERROR),
            ("info", "x"),
            ("error", KEY_ERROR),
        ]
    finally:
        await supervisor.stop()


async def test_start_with_only_unreadable_keys_says_nothing_about_no_instances(
    db: None, no_network: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
            " VALUES (?, 'Lost', 'sonarr', 'http://s2:8989', ?)",
            (LOST, encrypt("old", OTHER_KEY)),
        )
        await conn.commit()
    supervisor = Supervisor(master_key=MASTER_KEY)
    caplog.set_level(logging.INFO, logger=supervisor_module.__name__)
    try:
        await supervisor.start()
    finally:
        await supervisor.stop()

    assert supervisor._tasks == {}
    assert "no enabled instances" not in caplog.text
    assert "cannot be decrypted" in caplog.text
    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM search_log WHERE message LIKE 'Supervisor started%'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_start_still_reports_no_enabled_instances(
    db: None, no_network: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    caplog.set_level(logging.WARNING, logger=supervisor_module.__name__)
    try:
        await supervisor.start()
    finally:
        await supervisor.stop()

    assert "no enabled instances configured" in caplog.text


async def test_enabling_or_reconciling_an_unreadable_instance_starts_nothing(
    seeded: None, no_network: list[int]
) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    try:
        assert await supervisor.start_instance_task(LOST) is False
        await supervisor.reconcile_instance(LOST)

        await update_instance(LOST_PAUSED, master_key=MASTER_KEY, enabled=True)
        await supervisor.reconcile_instance(LOST_PAUSED)

        assert supervisor._tasks == {}
        assert no_network == []
        assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]
        assert await _rows(LOST_PAUSED) == [("error", KEY_ERROR, None, None)]
    finally:
        await supervisor.stop()


async def test_run_now_refuses_an_unreadable_key(seeded: None, no_network: list[int]) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    try:
        assert await supervisor.trigger_run_now(LOST) == "key_unreadable"
        assert await supervisor.trigger_run_now(LOST_PAUSED) == "disabled"
        assert supervisor._manual_runs == {}
    finally:
        await supervisor.stop()


async def test_entering_the_key_again_closes_the_error_and_starts_the_loop(
    seeded: None, no_network: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    caplog.set_level(logging.INFO, logger=supervisor_module.__name__)
    try:
        await supervisor.start()
        await update_instance(LOST, master_key=MASTER_KEY, api_key="new")

        await supervisor.reconcile_instance(LOST)

        assert set(supervisor._tasks) == {READABLE, LOST}
        assert no_network == [READABLE, LOST]
        assert await _rows(LOST) == [
            ("error", KEY_ERROR, None, None),
            ("info", "'Lost' API key can be decrypted again", None, None),
        ]
        assert "'Lost' API key can be decrypted again" in caplog.text
    finally:
        await supervisor.stop()


async def test_the_snapshot_refresh_skips_an_unreadable_key_and_restores_its_error(
    seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    refreshed: list[int] = []

    async def record_refresh(self: Supervisor, instance: Instance) -> None:
        refreshed.append(instance.core.id)

    monkeypatch.setattr(Supervisor, "_refresh_one_snapshot", record_refresh)
    supervisor = Supervisor(master_key=MASTER_KEY)

    await supervisor._refresh_all_snapshots_once()
    await supervisor._refresh_all_snapshots_once()

    assert refreshed == [READABLE, READABLE]
    assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]
    assert await _rows(LOST_PAUSED) == []

    # A retention purge or Clear logs removes the row; the next refresh puts it back.
    async with get_db() as conn:
        await conn.execute("DELETE FROM search_log")
        await conn.commit()
    await supervisor._refresh_all_snapshots_once()

    assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]


async def test_the_snapshot_refresh_leaves_a_key_entered_meanwhile_alone(
    seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rekey_during_refresh(self: Supervisor, instance: Instance) -> None:
        await update_instance(LOST, master_key=MASTER_KEY, api_key="new")

    monkeypatch.setattr(Supervisor, "_refresh_one_snapshot", rekey_during_refresh)
    supervisor = Supervisor(master_key=MASTER_KEY)

    await supervisor._refresh_all_snapshots_once()

    assert await _rows(LOST) == []


async def test_a_snapshot_refresh_never_builds_a_client_for_an_unreadable_key(
    seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_adapter(*args: object) -> None:
        raise AssertionError("built a client for an unreadable key")

    monkeypatch.setattr(supervisor_module, "get_adapter", no_adapter)
    supervisor = Supervisor(master_key=MASTER_KEY)

    await supervisor._refresh_one_snapshot(await _flagged(LOST))


async def test_a_cycle_never_searches_with_an_unreadable_key(
    seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_search(*args: object, **kwargs: object) -> None:
        raise AssertionError("searched with an unreadable key")

    monkeypatch.setattr(supervisor_module, "run_instance_search", no_search)
    supervisor = Supervisor(master_key=MASTER_KEY)

    outcome = await supervisor._run_search_cycle(await _flagged(LOST), cycle_trigger="scheduled")

    assert outcome is CycleOutcome.failed
    assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]


async def test_a_loop_ends_when_its_key_cannot_be_decrypted(
    seeded: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def no_cycle(*args: object, **kwargs: object) -> None:
        raise AssertionError("ran a cycle with an unreadable key")

    monkeypatch.setattr(supervisor_module, "_STARTUP_GRACE_SECS", 0)
    monkeypatch.setattr(Supervisor, "_run_search_cycle", no_cycle)
    supervisor = Supervisor(master_key=MASTER_KEY)
    caplog.set_level(logging.WARNING, logger=supervisor_module.__name__)

    await asyncio.wait_for(supervisor._instance_loop(LOST), timeout=5)
    await asyncio.wait_for(supervisor._run_manual_once(LOST), timeout=5)

    assert "'Lost'" in caplog.text
    assert "cannot be decrypted" in caplog.text
    assert await _rows(LOST) == [("error", KEY_ERROR, None, None)]


async def test_a_readable_instance_starts_without_a_closing_row(
    seeded: None, no_network: list[int]
) -> None:
    supervisor = Supervisor(master_key=MASTER_KEY)
    try:
        assert await supervisor.start_instance_task(READABLE) is True
        assert await _rows(READABLE) == []
    finally:
        await supervisor.stop()

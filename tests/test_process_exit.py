"""Houndarr exits when startup fails or shutdown is cut short (#915).

Each aiosqlite connection runs a non-daemon worker thread that the
interpreter joins at exit, so one connection left open keeps the process
alive after uvicorn or the CLI has given up.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import subprocess
import sys
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from aiosqlitepool.connection import PoolConnection
from cryptography.fernet import Fernet, InvalidToken

from houndarr import app as app_module
from houndarr import database
from houndarr.config import AppSettings
from houndarr.crypto import encrypt, ensure_master_key
from houndarr.database import close_all_pools, get_db, init_db, set_db_path
from houndarr.engine import supervisor as supervisor_module
from houndarr.engine.supervisor import Supervisor
from houndarr.errors import ServiceError
from houndarr.services.admin import factory_reset

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUPERVISOR_TASK_PREFIXES = ("search-loop-", "snapshot-")


@pytest_asyncio.fixture()
async def opened(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[list[aiosqlite.Connection]]:
    """Record every aiosqlite connection the test opens."""
    connections: list[aiosqlite.Connection] = []
    real_connect = aiosqlite.connect

    def recording_connect(*args: Any, **kwargs: Any) -> aiosqlite.Connection:
        conn = real_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(aiosqlite, "connect", recording_connect)
    yield connections
    await close_all_pools()
    # Stop what a failing test leaked, or the xdist worker never exits.
    for conn in connections:
        if conn._thread.is_alive():
            conn.stop()
            conn._thread.join(timeout=5)


def _assert_workers_stopped(connections: list[aiosqlite.Connection]) -> None:
    assert connections, "the test opened no connection"
    for conn in connections:
        conn._thread.join(timeout=5)
    assert [conn for conn in connections if conn._thread.is_alive()] == []


async def _borrow() -> None:
    async with get_db() as conn:
        await conn.execute("SELECT 1")


async def _seed_instance(db_path: Path, key: bytes) -> None:
    set_db_path(str(db_path))
    await init_db()
    try:
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO instances (name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?)",
                ("Sonarr", "sonarr", "http://sonarr.invalid:8989", encrypt("api-key", key)),
            )
            await conn.commit()
    finally:
        await close_all_pools()


def _supervisor_tasks() -> list[asyncio.Task[Any]]:
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith(_SUPERVISOR_TASK_PREFIXES)
    ]


async def _cancel(tasks: Iterable[asyncio.Task[Any] | None]) -> None:
    pending = [task for task in tasks if task is not None and not task.done()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _block_forever(*args: object, **kwargs: object) -> None:
    await asyncio.Event().wait()


# Connection setup


async def test_a_file_that_is_not_sqlite_leaves_no_worker_running(
    opened: list[aiosqlite.Connection], tmp_path: Path
) -> None:
    db_path = tmp_path / "houndarr.db"
    db_path.write_bytes(b"this is not a database\n" * 64)
    set_db_path(str(db_path))

    with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
        await _borrow()

    _assert_workers_stopped(opened)


async def test_a_cancel_during_connection_setup_leaves_no_worker_running(
    opened: list[aiosqlite.Connection], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_db_path(str(tmp_path / "houndarr.db"))
    entered = asyncio.Event()

    async def blocked_execute(self: aiosqlite.Connection, *args: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(aiosqlite.Connection, "execute", blocked_execute)
    task = asyncio.create_task(_borrow())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # ``opened`` holds the connection, so its __del__ cannot stop the thread
    # and hide the leak.
    _assert_workers_stopped(opened)


async def test_a_cancel_during_the_pool_health_check_leaves_no_worker_running(
    opened: list[aiosqlite.Connection], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_db_path(str(tmp_path / "houndarr.db"))
    entered = asyncio.Event()

    async def blocked_is_alive(self: PoolConnection) -> bool:
        entered.set()
        await asyncio.Event().wait()
        return True

    # aiosqlitepool neither registers nor closes a new connection whose
    # health check is cancelled.
    monkeypatch.setattr(PoolConnection, "is_alive", blocked_is_alive)
    task = asyncio.create_task(_borrow())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await close_all_pools()
    _assert_workers_stopped(opened)


async def test_close_all_pools_closes_a_pool_opened_while_it_runs(
    opened: list[aiosqlite.Connection], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_db_path(str(tmp_path / "houndarr.db"))
    await _borrow()
    pool = database._pools[str(tmp_path / "houndarr.db")]
    real_close = pool.close

    async def close_then_borrow() -> None:
        await real_close()
        await _borrow()

    monkeypatch.setattr(pool, "close", close_then_borrow)
    await close_all_pools()

    assert database._pools == {}
    _assert_workers_stopped(opened)


@pytest.mark.parametrize("ticks", [0, 1, 2, 3, 4])
async def test_overlapping_close_all_pools_calls_both_return(
    opened: list[aiosqlite.Connection], tmp_path: Path, ticks: int
) -> None:
    # Two factory resets in flight, or a cancelled reset and the lifespan exit.
    set_db_path(str(tmp_path / "houndarr.db"))
    await _borrow()
    first = asyncio.create_task(close_all_pools())
    for _ in range(ticks):
        await asyncio.sleep(0)

    await asyncio.wait_for(asyncio.gather(first, close_all_pools()), timeout=5)

    _assert_workers_stopped(opened)


# Lifespan


@pytest.mark.parametrize("step", ["init_db_migrations", "list_instances"])
async def test_a_startup_failure_closes_the_database(
    opened: list[aiosqlite.Connection],
    test_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
) -> None:
    async def failing_step(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"{step} failed")

    monkeypatch.setattr(app_module, step, failing_step)
    app = app_module.create_app()

    with pytest.raises(RuntimeError, match=f"{step} failed"):
        async with app.router.lifespan_context(app):
            pass

    assert str(test_settings.db_path) not in database._pools
    _assert_workers_stopped(opened)


async def test_a_master_key_that_does_not_match_fails_startup_and_closes_the_database(
    opened: list[aiosqlite.Connection], test_settings: AppSettings
) -> None:
    await _seed_instance(test_settings.db_path, Fernet.generate_key())
    app = app_module.create_app()

    with pytest.raises(InvalidToken):
        async with app.router.lifespan_context(app):
            pass

    assert str(test_settings.db_path) not in database._pools
    _assert_workers_stopped(opened)


async def test_a_partial_supervisor_start_leaves_no_task_running(
    opened: list[aiosqlite.Connection],
    test_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_instance(test_settings.db_path, ensure_master_key(test_settings.data_dir))

    async def failing_write_log(**kwargs: object) -> None:
        raise RuntimeError("search_log write failed")

    monkeypatch.setattr(Supervisor, "_instance_loop", _block_forever)
    monkeypatch.setattr(Supervisor, "_refresh_one_snapshot", _block_forever)
    monkeypatch.setattr(supervisor_module, "_write_log", failing_write_log)
    app = app_module.create_app()

    try:
        with pytest.raises(RuntimeError, match="search_log write failed"):
            async with app.router.lifespan_context(app):
                pass

        assert _supervisor_tasks() == []
        assert str(test_settings.db_path) not in database._pools
        _assert_workers_stopped(opened)
    finally:
        await _cancel(_supervisor_tasks())


async def test_a_cancel_while_serving_closes_the_database(
    opened: list[aiosqlite.Connection], test_settings: AppSettings
) -> None:
    app = app_module.create_app()
    serving = asyncio.Event()

    async def serve() -> None:
        async with app.router.lifespan_context(app):
            serving.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(serve())
    await serving.wait()
    supervisor = app.state.supervisor
    try:
        # uvicorn skips the lifespan shutdown on a second Ctrl-C and the
        # event loop cancels the lifespan at the yield instead.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert supervisor._snapshot_task is None
        assert app.state.rate_limit_sweep_task.done()
        assert str(test_settings.db_path) not in database._pools
        _assert_workers_stopped(opened)
    finally:
        await supervisor.stop()
        await _cancel([app.state.rate_limit_sweep_task, app.state.retention_task])


async def test_a_failing_stop_is_logged_and_the_database_still_closes(
    opened: list[aiosqlite.Connection],
    test_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_stop(self: Supervisor) -> None:
        raise RuntimeError("supervisor stop failed")

    caplog.set_level(logging.ERROR, logger="houndarr.app")
    app = app_module.create_app()
    try:
        async with app.router.lifespan_context(app):
            monkeypatch.setattr(Supervisor, "stop", failing_stop)

        assert "Error stopping background work" in caplog.text
        assert str(test_settings.db_path) not in database._pools
        _assert_workers_stopped(opened)
    finally:
        await _cancel(_supervisor_tasks())


async def test_shutdown_after_a_factory_reset_stops_the_new_background_work(
    opened: list[aiosqlite.Connection], test_settings: AppSettings
) -> None:
    app = app_module.create_app()
    async with app.router.lifespan_context(app):
        await factory_reset(app=app, data_dir=test_settings.data_dir)
        supervisor = app.state.supervisor
        snapshot_task = supervisor._snapshot_task
        sweep_task = app.state.rate_limit_sweep_task
        retention_task = app.state.retention_task

    try:
        assert snapshot_task is not None
        assert snapshot_task.done()
        assert sweep_task.done()
        assert retention_task is not None
        assert retention_task.done()
        assert str(test_settings.db_path) not in database._pools
        _assert_workers_stopped(opened)
    finally:
        await supervisor.stop()
        await _cancel([sweep_task, retention_task])


async def test_shutdown_after_a_failed_factory_reset_start_stops_the_new_supervisor(
    opened: list[aiosqlite.Connection],
    test_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_start = Supervisor.start
    started: list[Supervisor] = []

    async def start_then_fail(self: Supervisor) -> None:
        await real_start(self)
        started.append(self)
        raise RuntimeError("supervisor start failed")

    app = app_module.create_app()
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(Supervisor, "start", start_then_fail)
        with pytest.raises(ServiceError):
            await factory_reset(app=app, data_dir=test_settings.data_dir)

    try:
        assert len(started) == 1
        assert started[0]._snapshot_task is None
        assert str(test_settings.db_path) not in database._pools
        _assert_workers_stopped(opened)
    finally:
        for supervisor in started:
            await supervisor.stop()


# The CLI process


def _run_cli(data_dir: Path) -> tuple[int, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HOUNDARR_", "PYTEST_"))
    }
    try:
        result = subprocess.run(
            # A regression that let startup pass would bind a free loopback port.
            [
                sys.executable,
                "-m",
                "houndarr",
                "--data-dir",
                str(data_dir),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
            ],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"houndarr was still running after {exc.timeout} s")
    return result.returncode, result.stdout + result.stderr


def test_cli_exits_when_the_database_file_is_not_sqlite(tmp_path: Path) -> None:
    (tmp_path / "houndarr.db").write_bytes(b"this is not a database\n" * 64)

    returncode, output = _run_cli(tmp_path)

    assert returncode == 1, output
    assert "file is not a database" in output


def test_cli_exits_when_the_master_key_does_not_match(tmp_path: Path) -> None:
    asyncio.run(_seed_instance(tmp_path / "houndarr.db", ensure_master_key(tmp_path)))
    (tmp_path / "houndarr.masterkey").unlink()

    returncode, output = _run_cli(tmp_path)

    assert returncode == 3, output
    assert "InvalidToken" in output
    assert "Application startup failed. Exiting." in output

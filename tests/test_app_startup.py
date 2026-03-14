"""Tests for application startup behavior in lifespan."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from houndarr.app import create_app
from houndarr.engine.supervisor import Supervisor


def test_startup_warns_when_no_instances(
    test_settings: object, caplog: pytest.LogCaptureFixture
) -> None:
    """App lifespan logs warning when no instances are configured."""
    assert test_settings is not None
    caplog.set_level(logging.WARNING)

    app = create_app()
    with TestClient(app, raise_server_exceptions=True):
        pass

    messages = [record.getMessage() for record in caplog.records]
    assert any("No instances configured" in message for message in messages)


def test_periodic_retention_runs_during_uptime(
    test_settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention purger runs at startup and periodically while app is running."""
    assert test_settings is not None
    from houndarr import app as app_module

    purges: list[int] = []

    async def _fake_purge_old_logs(retention_days: int) -> int:
        purges.append(retention_days)
        return 0

    async def _fake_start(self: Supervisor) -> None:
        return None

    async def _fake_stop(self: Supervisor) -> None:
        return None

    monkeypatch.setattr(app_module, "purge_old_logs", _fake_purge_old_logs)
    monkeypatch.setattr(app_module, "_LOG_RETENTION_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(Supervisor, "start", _fake_start)
    monkeypatch.setattr(Supervisor, "stop", _fake_stop)

    app = create_app()
    with TestClient(app, raise_server_exceptions=True):
        time.sleep(0.07)

    # At least one startup purge + at least one periodic purge.
    assert len(purges) >= 2

"""Tests for application startup behavior in lifespan."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from houndarr.app import create_app


def test_startup_warns_when_no_instances(test_settings: object, caplog: object) -> None:
    """App lifespan logs warning when no instances are configured."""
    assert test_settings is not None
    assert caplog is not None

    caplog.set_level(logging.WARNING)

    app = create_app()
    with TestClient(app, raise_server_exceptions=True):
        pass

    messages = [record.getMessage() for record in caplog.records]
    assert any("No instances configured" in message for message in messages)

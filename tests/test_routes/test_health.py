"""Tests for GET /api/health.

The healthcheck backs the Docker ``HEALTHCHECK`` instruction and must
respond 200 with a stable JSON body regardless of whether the first-run
setup has completed.  Any change that breaks either property would
cause live containers to be marked unhealthy.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok_json(app: TestClient) -> None:
    resp = app.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_is_reachable_before_setup(app: TestClient) -> None:
    """Docker probes hit /api/health before the operator visits /setup."""
    resp = app.get("/api/health")
    assert resp.status_code == 200


def test_health_has_json_content_type(app: TestClient) -> None:
    resp = app.get("/api/health")
    assert resp.headers["content-type"].startswith("application/json")

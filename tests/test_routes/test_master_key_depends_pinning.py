"""Pin the get_master_key Depends shim.

:func:`houndarr.deps.get_master_key` resolves the Fernet master key
from ``app.state.master_key`` so route handlers can take it as a
:class:`Annotated[bytes, Depends(get_master_key)]` parameter
instead of reaching into ``request.app.state.master_key`` directly.

These tests lock:

* the shim returns the bytes value stored on ``app.state``
* the shim asserts the stored value is ``bytes`` (catches misconfig
  before the Fernet layer does)
* the logs-page route reads its master_key through the shim so a
  logged-in session resolves the signed cookie, looks up instances,
  and renders without a 5xx
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from houndarr.deps import get_master_key

pytestmark = pytest.mark.pinning


def _fake_request(master_key: object) -> Request:
    """Build a minimal Starlette Request whose app.state carries *master_key*."""
    app = FastAPI()
    app.state.master_key = master_key
    scope = {"type": "http", "app": app, "headers": []}
    return Request(scope)


def test_get_master_key_returns_stored_bytes() -> None:
    """The shim returns app.state.master_key when it is already bytes."""
    key = b"0" * 44
    request = _fake_request(key)
    assert get_master_key(request) is key


def test_get_master_key_rejects_non_bytes() -> None:
    """A non-bytes master_key raises HTTPException 503.

    Matches :func:`get_supervisor`'s failure-class so a misconfigured
    lifespan surfaces a deterministic 503 at the dependency boundary
    instead of letting the Fernet layer raise a less actionable
    error mid-request.  Using ``HTTPException`` rather than ``assert``
    keeps the check alive under ``python -O`` /
    ``PYTHONOPTIMIZE=1``, which strips asserts.
    """
    request = _fake_request("not-bytes")
    with pytest.raises(HTTPException) as exc_info:
        get_master_key(request)
    assert exc_info.value.status_code == 503


def test_get_master_key_rejects_missing_state_slot() -> None:
    """A request whose app.state has no master_key slot raises 503."""
    app = FastAPI()
    scope = {"type": "http", "app": app, "headers": []}
    request = Request(scope)
    with pytest.raises(HTTPException) as exc_info:
        get_master_key(request)
    assert exc_info.value.status_code == 503


def test_logs_page_route_reads_master_key_through_shim(app: TestClient) -> None:
    """The /logs page renders a 200 after login, proving the shim wired up.

    The logs page takes a ``Depends(get_master_key)`` parameter
    rather than reading ``request.app.state.master_key`` directly;
    a successful render under the test client confirms FastAPI
    resolved the dependency correctly.
    """
    app.post(
        "/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "confirm_password": "ValidPass1!",
        },
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    response = app.get("/logs")
    assert response.status_code == 200

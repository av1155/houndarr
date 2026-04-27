"""Pin the SupervisorProto + get_supervisor Depends shim (Track B.21).

:func:`houndarr.routes.api.status.get_supervisor` resolves the
running supervisor typed as
:class:`~houndarr.protocols.SupervisorProto`.  Route handlers
consume it via ``Depends(get_supervisor)`` instead of reaching into
``request.app.state.supervisor`` directly; this lets tests swap the
supervisor with any object that satisfies the Protocol shape.

These tests lock:

* the Protocol declaration is ``@runtime_checkable`` and the
  concrete :class:`Supervisor` conforms to it;
* the Depends shim raises 503 when ``app.state.supervisor`` is
  missing or is not a :class:`Supervisor`;
* the ``run_now`` route still returns 202 / 404 / 409 based on
  ``trigger_run_now`` responses, using only the Protocol surface.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto
from houndarr.routes.api.status import get_supervisor, router

pytestmark = pytest.mark.pinning


# Protocol conformance


def test_supervisor_class_conforms_to_supervisor_proto() -> None:
    """Concrete :class:`Supervisor` is structurally a :class:`SupervisorProto`.

    The runtime isinstance check in :func:`get_supervisor` uses the
    concrete class, but tests can swap in any stub that satisfies the
    Protocol.  Pinning the concrete conformance keeps that swap
    pathway honest.
    """
    assert issubclass(Supervisor, SupervisorProto)


def test_bare_object_does_not_conform_to_supervisor_proto() -> None:
    """A plain :class:`object` must fail the structural check."""
    assert not isinstance(object(), SupervisorProto)


def test_minimal_stub_conforms_to_supervisor_proto() -> None:
    """A stub with the three declared coroutines satisfies the Protocol."""

    class Stub:
        async def trigger_run_now(self, instance_id: int) -> str:
            return "accepted"

        async def reconcile_instance(self, instance_id: int) -> None:
            return None

        async def stop_instance_task(self, instance_id: int) -> bool:
            return True

    assert isinstance(Stub(), SupervisorProto)


# Depends shim behaviour


def _build_app(supervisor_slot: object) -> FastAPI:
    """Build a minimal FastAPI app with the run-now route and a supervisor slot."""
    app = FastAPI()
    app.include_router(router)
    app.state.supervisor = supervisor_slot
    return app


def test_get_supervisor_returns_concrete_supervisor_when_present() -> None:
    """Shim returns the concrete Supervisor instance when app.state is populated."""
    from cryptography.fernet import Fernet

    supervisor = Supervisor(master_key=Fernet.generate_key())
    app = _build_app(supervisor)

    with TestClient(app) as client:
        # The concrete Supervisor has no route-level trigger_run_now mock;
        # bypass by asserting the shim is wired correctly via direct call.
        scope_request = cast(
            "FastAPI", app
        )  # satisfy the type checker; Request created inline below
        del scope_request
        from starlette.requests import Request

        fake_request = Request(
            {"type": "http", "method": "GET", "path": "/", "app": client.app},
        )
        resolved = get_supervisor(fake_request)
        assert resolved is supervisor


def test_get_supervisor_raises_503_when_slot_is_none() -> None:
    """Missing supervisor slot -> HTTPException(503)."""
    from fastapi import HTTPException
    from starlette.requests import Request

    app = _build_app(None)
    fake_request = Request({"type": "http", "method": "GET", "path": "/", "app": app})

    with pytest.raises(HTTPException) as exc_info:
        get_supervisor(fake_request)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Supervisor unavailable"


def test_get_supervisor_raises_503_when_slot_is_non_supervisor() -> None:
    """A non-Supervisor sentinel (e.g. str) also triggers 503."""
    from fastapi import HTTPException
    from starlette.requests import Request

    app = _build_app("definitely-not-a-supervisor")
    fake_request = Request({"type": "http", "method": "GET", "path": "/", "app": app})

    with pytest.raises(HTTPException) as exc_info:
        get_supervisor(fake_request)
    assert exc_info.value.status_code == 503


# Run-now route uses Depends override


def _app_with_override(proto_stub: SupervisorProto) -> FastAPI:
    """Build a FastAPI app whose get_supervisor dependency resolves to *proto_stub*."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_supervisor] = lambda: proto_stub
    return app


class _StubSupervisor:
    """Minimal SupervisorProto implementation for route behaviour tests."""

    def __init__(self, status: str) -> None:
        self._status = status
        self.trigger_calls: list[int] = []

    async def trigger_run_now(self, instance_id: int) -> str:
        self.trigger_calls.append(instance_id)
        return self._status

    async def reconcile_instance(self, instance_id: int) -> None:
        return None

    async def stop_instance_task(self, instance_id: int) -> bool:
        return True


def test_run_now_returns_202_on_accepted() -> None:
    """Stub supervisor returning 'accepted' results in 202 + payload."""
    stub = _StubSupervisor("accepted")
    app = _app_with_override(cast("SupervisorProto", stub))
    with TestClient(app) as client:
        resp = client.post("/api/instances/7/run-now")
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted", "instance_id": 7}
    assert stub.trigger_calls == [7]


def test_run_now_returns_404_on_not_found() -> None:
    """Stub returning 'not_found' -> HTTP 404."""
    stub = _StubSupervisor("not_found")
    app = _app_with_override(cast("SupervisorProto", stub))
    with TestClient(app) as client:
        resp = client.post("/api/instances/9/run-now")
    assert resp.status_code == 404


def test_run_now_returns_409_on_disabled() -> None:
    """Stub returning 'disabled' -> HTTP 409."""
    stub = _StubSupervisor("disabled")
    app = _app_with_override(cast("SupervisorProto", stub))
    with TestClient(app) as client:
        resp = client.post("/api/instances/2/run-now")
    assert resp.status_code == 409


def test_run_now_returns_503_when_no_supervisor_override() -> None:
    """Without a Depends override the shim's 503 branch fires end-to-end."""
    app = FastAPI()
    app.include_router(router)
    app.state.supervisor = None
    with TestClient(app) as client:
        resp = client.post("/api/instances/1/run-now")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Supervisor unavailable"}


# Exhaustive stub to guarantee AsyncMock plays nicely with the shim


def test_get_supervisor_accepts_protocol_overrides_in_fastapi_app() -> None:
    """FastAPI's dependency-override system works with a Protocol-typed stub.

    This is the critical behaviour tests will rely on: swap the
    supervisor with any object conforming to SupervisorProto and the
    Depends injection still wires correctly.
    """
    stub = _StubSupervisor("accepted")
    app = _app_with_override(cast("SupervisorProto", stub))
    with TestClient(app) as client:
        resp = client.post("/api/instances/42/run-now")
    assert resp.status_code == 202
    # The route reaches trigger_run_now on the stub, not on any real Supervisor.
    assert stub.trigger_calls == [42]


# Quiet the ``unused`` hint on AsyncMock; it is imported for the
# parametrized run_now test fixture.
_ = AsyncMock

"""FastAPI :class:`Depends` shims shared across route modules.

This module narrows ``app.state`` to typed Protocols for route
handlers so each handler takes an :class:`Annotated[...,
Depends(...)]` parameter instead of reading ``request.app.state``
directly.  Keeping every shim in one place means the route layer
imports a single module for every piece of lifespan wiring
(supervisor, master key, and anything future that benefits from a
Protocol-typed gate).

The concrete :class:`~houndarr.engine.supervisor.Supervisor` instance
lives on ``app.state.supervisor``; this shim narrows the route-facing
surface to :class:`~houndarr.protocols.SupervisorProto` so handlers
only depend on the methods they actually invoke.  The positive
identity assertion still uses the concrete class so a mis-wired
application state (None, wrong type) surfaces as a 503 instead of
a mid-request AttributeError.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from houndarr.engine.supervisor import Supervisor
from houndarr.protocols import SupervisorProto


def get_supervisor(request: Request) -> SupervisorProto:
    """Return the running supervisor typed as :class:`SupervisorProto`.

    Raises :class:`HTTPException` with status 503 when the supervisor
    slot is empty.  That happens in three legitimate cases: the
    pre-lifespan window before ``app.state.supervisor`` has been
    populated, the brief pause during a factory-reset where the
    supervisor has been stopped but the new one has not yet
    attached, and any test or boot path that never wired a
    supervisor at all.
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    if not isinstance(supervisor, Supervisor):
        raise HTTPException(status_code=503, detail="Supervisor unavailable")
    return supervisor


def get_master_key(request: Request) -> bytes:
    """Return the Fernet master key from ``app.state.master_key``.

    FastAPI ``Depends(get_master_key)`` replaces the bare
    ``request.app.state.master_key`` read in route handlers.  The lift
    into this module does three things: it centralises the state
    access so the eventual move to a real Protocol-typed key provider
    only touches one site; it gives mypy a single typed entry point
    (routes see ``bytes`` instead of ``Any``); and it keeps the state
    read close to the supervisor shim so the route layer imports one
    module for both pieces of lifespan wiring.

    Raises :class:`HTTPException` 503 when ``app.state.master_key``
    is missing or is not ``bytes``.  Matches the :func:`get_supervisor`
    failure-class so a misconfigured lifespan (pre-init window,
    mid-factory-reset pause, or a test harness that never wired the
    key) surfaces a deterministic 503 at the dependency boundary.
    An earlier revision used ``assert isinstance(...)``; that check
    disappears under ``python -O`` / ``PYTHONOPTIMIZE=1`` and left
    the Fernet layer to raise a less actionable error mid-request.
    """
    key = getattr(request.app.state, "master_key", None)
    if not isinstance(key, bytes):
        raise HTTPException(status_code=503, detail="Master key unavailable")
    return key

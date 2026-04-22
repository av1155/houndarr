"""FastAPI :class:`Depends` shims shared across route modules.

Track D.12 introduces this module.  The intent is a single place
where ``app.state`` gets narrowed to a typed Protocol for route
handlers so the handler can take a :class:`Annotated[...,
Depends(...)]` parameter instead of a direct ``request.app.state``
read.  D.12 migrates :func:`get_supervisor` (previously declared
inline at :mod:`houndarr.routes.api.status`) to this module;
later D batches migrate the master-key reader, the settings-route
helpers that still reach into ``request.app.state``, and any other
per-request state that benefits from a Protocol-typed gate.

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

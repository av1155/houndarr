"""Structural Protocol mirroring the AppAdapter dataclass shape.

Track B.18 declaration.  The :class:`AppAdapter` dataclass today
holds six callables.  This Protocol captures the same shape so
future Track C.10 can migrate the registry to Protocol-typed class
instances without a call-site cascade.

Runtime-checkable so tests can ``isinstance(adapter, AppAdapterProto)``
as a conformance check when the registry is rewired.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from houndarr.clients.base import ArrClient
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance


@runtime_checkable
class AppAdapterProto(Protocol):
    """Structural contract every adapter (module or class) must satisfy."""

    adapt_missing: Callable[..., SearchCandidate]
    adapt_cutoff: Callable[..., SearchCandidate]
    adapt_upgrade: Callable[..., SearchCandidate]
    fetch_upgrade_pool: Callable[..., Awaitable[list[Any]]]
    dispatch_search: Callable[..., Awaitable[None]]
    make_client: Callable[[Instance], ArrClient]

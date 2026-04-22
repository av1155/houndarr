"""Structural Protocol mirroring the AppAdapter dataclass shape.

Track B.18 declaration.  The :class:`AppAdapter` dataclass today
holds six callables.  This Protocol captures the same shape so
future Track C.10 can migrate the registry to Protocol-typed class
instances without a call-site cascade.

Runtime-checkable so tests can ``isinstance(adapter, AppAdapterProto)``
as a conformance check when the registry is rewired.

Each member is declared via ``@property`` so the Protocol advertises
read-only attributes.  That matters because :class:`AppAdapter` is a
frozen dataclass: its slots are read-only at runtime, and a
bare-attribute Protocol would reject frozen instances as
non-conforming.  Future class-based adapters can expose the same
callables as plain attributes or as properties; either form
satisfies this Protocol.
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

    @property
    def adapt_missing(self) -> Callable[..., SearchCandidate]:
        """Build a :class:`SearchCandidate` from a raw missing-pass item."""

    @property
    def adapt_cutoff(self) -> Callable[..., SearchCandidate]:
        """Build a :class:`SearchCandidate` from a raw cutoff-unmet item."""

    @property
    def adapt_upgrade(self) -> Callable[..., SearchCandidate]:
        """Build a :class:`SearchCandidate` from a raw upgrade-pool item."""

    @property
    def fetch_upgrade_pool(self) -> Callable[..., Awaitable[list[Any]]]:
        """Fetch the per-cycle upgrade candidate list from the *arr app."""

    @property
    def dispatch_search(self) -> Callable[..., Awaitable[None]]:
        """Send the *arr search command for one candidate."""

    @property
    def make_client(self) -> Callable[[Instance], ArrClient]:
        """Return a fresh (unopened) :class:`ArrClient` for *instance*."""

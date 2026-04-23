"""Structural Protocol for the per-app adapter shape.

Every adapter (per-app class with six staticmethod attributes) must
satisfy this Protocol.  Runtime-checkable so tests can
``isinstance(adapter, AppAdapterProto)`` as a conformance check
against the :data:`ADAPTERS` registry.

Each member is declared via ``@property`` so the Protocol advertises
read-only attributes.  That shape accepts both class-based adapters
(staticmethod attributes on the class) and future frozen-dataclass
forms whose slots are read-only at runtime; a bare-attribute
Protocol would reject the latter as non-conforming.
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

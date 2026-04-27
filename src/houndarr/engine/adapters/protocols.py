"""Structural Protocol mirroring the AppAdapter dataclass shape.

Every adapter (per-app class with eight staticmethod attributes) must
satisfy this Protocol.  Runtime-checkable so tests can
``isinstance(adapter, AppAdapterProto)`` as a conformance check
against the :data:`ADAPTERS` registry.

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

from houndarr.clients.base import ArrClient, InstanceSnapshot, ReconcileSets
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

    @property
    def fetch_reconcile_sets(self) -> Callable[..., Awaitable[ReconcileSets]]:
        """Return the authoritative ``(item_type, item_id)`` sets per pass.

        Called from the supervisor's snapshot refresh; the returned
        :class:`~houndarr.clients.base.ReconcileSets` drive the cooldown
        reconciliation that reaps rows for items no longer wanted.
        Implementations paginate ``/wanted/missing`` and
        ``/wanted/cutoff`` for leaf ids, call :attr:`fetch_upgrade_pool`
        for upgrade-pool ids, and (in context-mode adapters) UNION in
        the synthetic parent ids derived from leaf parent metadata so
        the DB match stays pure set membership.
        """

    @property
    def fetch_instance_snapshot(self) -> Callable[..., Awaitable[InstanceSnapshot]]:
        """Return the per-instance dashboard counts.

        Called from the supervisor's snapshot refresh just before
        :attr:`fetch_reconcile_sets`.  The returned
        :class:`~houndarr.clients.base.InstanceSnapshot` carries the
        ``monitored_total`` (missing + cutoff totals) and
        ``unreleased_count`` (monitored items whose canonical release
        anchor is strictly in the future).  Each *arr exposes a
        different shape, so anchor selection lives per-adapter; the
        five ``/wanted``-paged adapters delegate to
        :func:`compute_default_snapshot` while Whisparr v3 walks its
        cached ``/api/v3/movie`` response inline.
        """

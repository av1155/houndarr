"""Structural Protocols for Houndarr's repository and factory seams.

Each Protocol advertises the shape of a repository or factory
boundary so service-layer callers depend on the structural contract
rather than the concrete SQL-executing module.  The concrete
implementations live in :mod:`houndarr.repositories`.

Every declaration is ``@runtime_checkable`` so tests can assert
conformance with ``isinstance(instance, InstanceRepository)``.
Signatures are deliberately minimal: each method covers one call
site the service layer uses today, and nothing more.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Literal, Protocol, runtime_checkable

from houndarr.clients.base import ArrClient
from houndarr.services.instances import Instance
from houndarr.value_objects import ItemRef

# ``RunNowStatus`` is duplicated here (rather than imported from
# ``houndarr.engine.supervisor``) so this Protocol module stays
# import-cheap: pulling the supervisor module transitively drags in
# asyncio task bookkeeping, httpx, and the search loop.  The concrete
# :class:`~houndarr.engine.supervisor.Supervisor` still exports the
# same Literal alias, and both resolve to an identical type.
RunNowStatus = Literal["accepted", "not_found", "disabled"]

# Repositories


@runtime_checkable
class SettingsRepository(Protocol):
    """Key-value settings storage backed by the ``settings`` table."""

    def get_setting(self, key: str) -> Awaitable[str | None]:
        """Return the stored value for *key*, or ``None`` if absent."""

    def set_setting(self, key: str, value: str) -> Awaitable[None]:
        """Insert or update *key* with *value*."""

    def delete_setting(self, key: str) -> Awaitable[None]:
        """Remove *key* if it exists (no error if already absent)."""


@runtime_checkable
class InstanceRepository(Protocol):
    """Instance CRUD boundary backing the ``instances`` table.

    Read methods take the Fernet ``master_key`` so the concrete
    repository can decrypt ``encrypted_api_key`` before returning an
    :class:`Instance`.  Write methods accept
    :class:`~houndarr.repositories.instances.InstanceInsert` and
    :class:`~houndarr.repositories.instances.InstanceUpdate` payload
    dataclasses; those types are structurally typed as :class:`Any`
    on the Protocol so consumers do not have to import the concrete
    repository module just to satisfy the Protocol.
    """

    def list_instances(self, *, master_key: bytes) -> Awaitable[list[Instance]]:
        """Return every instance ordered by id ascending."""

    def get_instance(self, instance_id: int, *, master_key: bytes) -> Awaitable[Instance | None]:
        """Return the instance identified by *instance_id*, or ``None``."""

    def insert_instance(
        self,
        payload: Any,
        *,
        master_key: bytes,
    ) -> Awaitable[int]:
        """Insert a new instance row and return the assigned primary key.

        *payload* is structurally typed as :class:`Any` so consumers
        do not import the concrete repository module just to satisfy
        the Protocol; the concrete implementation takes an
        :class:`~houndarr.repositories.instances.InstanceInsert`.
        """

    def update_instance(
        self,
        instance_id: int,
        payload: Any,
        *,
        master_key: bytes,
    ) -> Awaitable[None]:
        """Partially update the instance identified by *instance_id*.

        *payload* is structurally typed as :class:`Any` for the same
        reason as :meth:`insert_instance`; the concrete implementation
        takes an :class:`~houndarr.repositories.instances.InstanceUpdate`
        and no-ops when every field is ``None``.
        """

    def delete_instance(self, instance_id: int) -> Awaitable[bool]:
        """Delete the instance row; return ``True`` iff a row was removed."""

    def update_instance_snapshot(
        self,
        instance_id: int,
        *,
        monitored_total: int,
        unreleased_count: int,
    ) -> Awaitable[None]:
        """Refresh the three v13 snapshot columns for *instance_id*."""


@runtime_checkable
class CooldownRepository(Protocol):
    """Cooldown SQL boundary backing the ``cooldowns`` table.

    The LRU skip-log sentinel (``should_log_skip``) stays in the
    service layer; this Protocol covers the three SQL methods the
    concrete repository exposes.
    """

    def exists_active_cooldown(self, ref: ItemRef, cooldown_days: int) -> Awaitable[bool]:
        """Return ``True`` when *ref* is within its cooldown window."""

    def upsert_cooldown(self, ref: ItemRef) -> Awaitable[None]:
        """Record *ref* as just-searched, upserting the existing row."""

    def delete_cooldowns_for_instance(self, instance_id: int) -> Awaitable[int]:
        """Delete every cooldown row for *instance_id* and return the count."""


@runtime_checkable
class SearchLogRepository(Protocol):
    """``search_log`` SQL boundary.

    The engine's :func:`_write_log` helper is a thin call to
    :meth:`insert_log_row`; the log-query service composes
    :meth:`fetch_log_rows` for the ``/api/logs`` route.
    """

    def insert_log_row(
        self,
        *,
        instance_id: int | None,
        item_id: int | None,
        item_type: str | None,
        action: str,
        search_kind: str | None = None,
        cycle_id: str | None = None,
        cycle_trigger: str | None = None,
        item_label: str | None = None,
        reason: str | None = None,
        message: str | None = None,
    ) -> Awaitable[None]:
        """Insert a single row into ``search_log``."""

    def fetch_log_rows(
        self,
        *,
        instance_id: int | None = None,
        action: str | None = None,
        search_kind: str | None = None,
        cycle_id: str | None = None,
        limit: int = 100,
        after_id: int | None = None,
    ) -> Awaitable[list[dict[str, Any]]]:
        """Return a filtered page of log rows."""

    def fetch_recent_searches(
        self,
        instance_id: int,
        *,
        search_kind: str,
        within_seconds: int,
    ) -> Awaitable[int]:
        """Count ``action='searched'`` rows in the trailing window."""

    def delete_logs_for_instance(self, instance_id: int) -> Awaitable[int]:
        """Delete every ``search_log`` row for *instance_id*."""


# Supervisor


@runtime_checkable
class SupervisorProto(Protocol):
    """Route-facing view of the engine supervisor.

    FastAPI routes depend on this structural contract instead of the
    concrete :class:`~houndarr.engine.supervisor.Supervisor` class.
    Only the methods route handlers invoke are declared; internal
    task bookkeeping and lifecycle helpers stay on the concrete
    type.  The shim in :mod:`houndarr.deps` wires the concrete
    instance into this Protocol for every route that depends on it.
    """

    async def trigger_run_now(self, instance_id: int) -> RunNowStatus:
        """Kick off one manual cycle for the instance identified by id."""

    async def reconcile_instance(self, instance_id: int) -> None:
        """Re-evaluate whether the instance should have a running task."""

    async def stop_instance_task(self, instance_id: int) -> bool:
        """Stop the instance's running task; return ``True`` if one was cancelled."""


# Client construction


@runtime_checkable
class ClientFactory(Protocol):
    """Construct an :class:`ArrClient` for a given :class:`Instance`.

    Each adapter exposes its own ``make_client`` function; the
    supervisor and search loop receive adapters via
    :func:`houndarr.engine.adapters.get_adapter` and call
    ``adapter.make_client(instance)`` directly.  The Protocol
    captures that signature so tests and future factory seams can
    depend on the structural contract.  Implementations must return
    an unopened client; callers open it via ``async with``.
    """

    def __call__(self, instance: Instance) -> ArrClient:
        """Build a fresh (unopened) :class:`ArrClient` for *instance*."""


# Re-exports for downstream imports


__all__ = [
    "ClientFactory",
    "CooldownRepository",
    "InstanceRepository",
    "RunNowStatus",
    "SearchLogRepository",
    "SettingsRepository",
    "SupervisorProto",
]

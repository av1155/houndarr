"""Adapter registry mapping instance types to their adapter functions.

Each :class:`AppAdapter` bundles the four functions the engine pipeline needs
to work with a specific *arr application: candidate conversion (missing and
cutoff), search dispatch, and client construction.

The :data:`ADAPTERS` dict is the single lookup table used by the pipeline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from houndarr.clients.base import ArrClient
from houndarr.engine.adapters import lidarr, radarr, readarr, sonarr, whisparr
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, InstanceType


@dataclass(frozen=True)
class AppAdapter:
    """Bundle of adapter functions for a single *arr application.

    Attributes:
        adapt_missing: Convert a raw missing item into a :class:`SearchCandidate`.
        adapt_cutoff: Convert a raw cutoff-unmet item into a :class:`SearchCandidate`.
        adapt_upgrade: Convert a library item into a :class:`SearchCandidate`
            for the upgrade pass.
        fetch_upgrade_pool: Fetch and filter the library for upgrade-eligible
            items.  Returns a list of app-specific library dataclasses.
        dispatch_search: Send the search command via the appropriate client method.
        make_client: Construct an (unopened) client for the application.
    """

    adapt_missing: Callable[..., SearchCandidate]
    adapt_cutoff: Callable[..., SearchCandidate]
    adapt_upgrade: Callable[..., SearchCandidate]
    fetch_upgrade_pool: Callable[..., Awaitable[list[Any]]]
    dispatch_search: Callable[..., Awaitable[None]]
    make_client: Callable[[Instance], ArrClient]


ADAPTERS: dict[InstanceType, AppAdapter] = {
    InstanceType.radarr: AppAdapter(
        adapt_missing=radarr.adapt_missing,
        adapt_cutoff=radarr.adapt_cutoff,
        adapt_upgrade=radarr.adapt_upgrade,
        fetch_upgrade_pool=radarr.fetch_upgrade_pool,
        dispatch_search=radarr.dispatch_search,
        make_client=radarr.make_client,
    ),
    InstanceType.sonarr: AppAdapter(
        adapt_missing=sonarr.adapt_missing,
        adapt_cutoff=sonarr.adapt_cutoff,
        adapt_upgrade=sonarr.adapt_upgrade,
        fetch_upgrade_pool=sonarr.fetch_upgrade_pool,
        dispatch_search=sonarr.dispatch_search,
        make_client=sonarr.make_client,
    ),
    InstanceType.lidarr: AppAdapter(
        adapt_missing=lidarr.adapt_missing,
        adapt_cutoff=lidarr.adapt_cutoff,
        adapt_upgrade=lidarr.adapt_upgrade,
        fetch_upgrade_pool=lidarr.fetch_upgrade_pool,
        dispatch_search=lidarr.dispatch_search,
        make_client=lidarr.make_client,
    ),
    InstanceType.readarr: AppAdapter(
        adapt_missing=readarr.adapt_missing,
        adapt_cutoff=readarr.adapt_cutoff,
        adapt_upgrade=readarr.adapt_upgrade,
        fetch_upgrade_pool=readarr.fetch_upgrade_pool,
        dispatch_search=readarr.dispatch_search,
        make_client=readarr.make_client,
    ),
    InstanceType.whisparr: AppAdapter(
        adapt_missing=whisparr.adapt_missing,
        adapt_cutoff=whisparr.adapt_cutoff,
        adapt_upgrade=whisparr.adapt_upgrade,
        fetch_upgrade_pool=whisparr.fetch_upgrade_pool,
        dispatch_search=whisparr.dispatch_search,
        make_client=whisparr.make_client,
    ),
}


def get_adapter(instance_type: InstanceType) -> AppAdapter:
    """Look up the adapter for *instance_type*.

    Args:
        instance_type: The :class:`InstanceType` to look up.

    Returns:
        The matching :class:`AppAdapter`.

    Raises:
        ValueError: If *instance_type* is not registered.
    """
    try:
        return ADAPTERS[instance_type]
    except KeyError:
        msg = f"No adapter registered for instance type: {instance_type!r}"
        raise ValueError(msg) from None

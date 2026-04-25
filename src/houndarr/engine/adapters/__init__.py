"""Adapter registry mapping instance types to their adapter classes.

Each per-app adapter module exposes an ``XAdapter`` class that
structurally satisfies
:class:`~houndarr.engine.adapters.protocols.AppAdapterProto` via eight
staticmethod attributes (``adapt_missing``, ``adapt_cutoff``,
``adapt_upgrade``, ``fetch_upgrade_pool``, ``dispatch_search``,
``make_client``, ``fetch_reconcile_sets``,
``fetch_instance_snapshot``).  The :data:`ADAPTERS` dict maps each
:class:`~houndarr.services.instances.InstanceType` to a single
process-lifetime instance of the matching adapter class; per-cycle
state lives on the *arr clients themselves, not on the adapters.

``AppAdapter`` is a backward-compatible alias for
:class:`AppAdapterProto` so callers can type-hint or
:func:`isinstance`-check against either name.
"""

from __future__ import annotations

from houndarr.engine.adapters import lidarr, radarr, readarr, sonarr, whisparr_v2, whisparr_v3
from houndarr.engine.adapters.protocols import AppAdapterProto
from houndarr.services.instances import InstanceType

# Backward-compatible alias for callers that type-hinted the registry
# via ``AppAdapter``.  The dataclass form is gone; the structural
# Protocol takes its place.
AppAdapter = AppAdapterProto


ADAPTERS: dict[InstanceType, AppAdapterProto] = {
    InstanceType.radarr: radarr.RadarrAdapter(),
    InstanceType.sonarr: sonarr.SonarrAdapter(),
    InstanceType.lidarr: lidarr.LidarrAdapter(),
    InstanceType.readarr: readarr.ReadarrAdapter(),
    InstanceType.whisparr_v2: whisparr_v2.WhisparrV2Adapter(),
    InstanceType.whisparr_v3: whisparr_v3.WhisparrV3Adapter(),
}


def get_adapter(instance_type: InstanceType) -> AppAdapterProto:
    """Look up the adapter for *instance_type*.

    Args:
        instance_type: The :class:`InstanceType` to look up.

    Returns:
        The matching adapter as an :class:`AppAdapterProto`-conforming
        class instance.

    Raises:
        ValueError: If *instance_type* is not registered.
    """
    try:
        return ADAPTERS[instance_type]
    except KeyError:
        msg = f"No adapter registered for instance type: {instance_type!r}"
        raise ValueError(msg) from None

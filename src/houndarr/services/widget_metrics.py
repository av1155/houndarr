"""Pure aggregation helpers for the external widget API."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any


def _to_int(value: Any) -> int:
    """Coerce dashboard JSON scalar values with the same tolerance as JavaScript Number."""
    if value is None:
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not isfinite(number):
        return 0
    return int(number)


def compute_widget_summary(envelope: Mapping[str, Any], searches_7d: int) -> dict[str, int]:
    """Return the stable widget totals from a dashboard status envelope.

    Eligible mirrors the dashboard card invariant: clamp each instance before
    summing so one stale cooldown overshoot cannot drain another instance's
    positive eligible count.
    """
    totals = {
        "eligible": 0,
        "gated": 0,
        "unreleased": 0,
        "upgrade": 0,
    }
    instances = envelope.get("instances", [])
    if not isinstance(instances, list):
        instances = []

    for instance in instances:
        if not isinstance(instance, Mapping):
            continue
        if not instance.get("enabled") or instance.get("active_error"):
            continue

        breakdown = instance.get("cooldown_breakdown", {})
        if not isinstance(breakdown, Mapping):
            breakdown = {}

        monitored = _to_int(instance.get("monitored_total"))
        missing = _to_int(breakdown.get("missing"))
        cutoff = _to_int(breakdown.get("cutoff"))
        upgrade = _to_int(breakdown.get("upgrade"))
        unreleased = _to_int(instance.get("unreleased_count"))
        gated = missing + cutoff

        totals["eligible"] += max(0, monitored - gated - unreleased)
        totals["gated"] += gated
        totals["unreleased"] += unreleased
        totals["upgrade"] += upgrade

    tracked = totals["eligible"] + totals["gated"] + totals["upgrade"] + totals["unreleased"]
    return {
        "tracked": tracked,
        "eligible": totals["eligible"],
        "gated": totals["gated"],
        "unreleased": totals["unreleased"],
        "searches_7d": max(0, _to_int(searches_7d)),
    }

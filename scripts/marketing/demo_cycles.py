"""Realistic search_log cycle fixtures for the marketing demo seed.

Each cycle mirrors what a real Houndarr run writes: a shared ``cycle_id``
per pass, realistic action mix (``searched`` + ``skipped`` with the exact
reason strings the engine emits + occasional ``error`` / ``info``), and a
range of ``search_kind`` and ``cycle_trigger`` values. The goal is to
make the Logs page screenshot look like a live instance rather than a
screen full of ``searched`` rows.

Skip-reason strings are kept in sync with ``src/houndarr/engine/``:
``on cooldown (Nd)``, ``on cutoff cooldown (Nd)``, ``on upgrade cooldown
(Nd)``, ``not yet released``, ``post-release grace (Nh)``, ``queue
backpressure (N/M)``, ``hourly cap reached (N)``. When an engine reason
string changes, update this fixture to match so the docs still reflect
what users see in practice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

# (instance_id, pool_key, pool_index, item_type, search_kind, action,
#  reason or None, message or None).  ``item_label`` resolves from the
#  pool at build time so the fixture stays in step with the title JSONs.
_CYCLE_SPECS: list[
    tuple[int, str, list[tuple[int | None, str, str, str, str, str | None, str | None]]]
] = [
    # minutes_ago, trigger, rows
    (
        12,
        "scheduled",
        [
            (1, "sonarr", 4, "episode", "missing", "searched", None, "dispatched"),
            (1, "sonarr", 0, "episode", "missing", "skipped", "on cooldown (14d)", None),
            (1, "sonarr", 1, "episode", "missing", "skipped", "on cooldown (14d)", None),
            (1, "sonarr", 2, "episode", "cutoff", "skipped", "on cutoff cooldown (21d)", None),
        ],
    ),
    (
        31,
        "scheduled",
        [
            (2, "radarr", 19, "movie", "missing", "searched", None, "dispatched"),
            (2, "radarr", 4, "movie", "missing", "skipped", "not yet released", None),
            (2, "radarr", 5, "movie", "missing", "skipped", "not yet released", None),
            (2, "radarr", 6, "movie", "missing", "skipped", "not yet released", None),
            (2, "radarr", 0, "movie", "missing", "skipped", "on cooldown (14d)", None),
        ],
    ),
    (
        58,
        "scheduled",
        [
            (1, "sonarr", 21, "episode", "upgrade", "searched", None, "dispatched"),
            (1, "sonarr", 22, "episode", "upgrade", "skipped", "on upgrade cooldown (90d)", None),
            (1, "sonarr", 23, "episode", "upgrade", "skipped", "on upgrade cooldown (90d)", None),
        ],
    ),
    (
        96,
        "scheduled",
        [
            (4, "lidarr", 0, "album", "cutoff", "searched", None, "dispatched"),
            (4, "lidarr", 1, "album", "cutoff", "skipped", "on cutoff cooldown (21d)", None),
            (4, "lidarr", 2, "album", "cutoff", "skipped", "on cutoff cooldown (21d)", None),
        ],
    ),
    (
        148,
        "scheduled",
        [
            (3, "radarr_4k", 2, "movie", "upgrade", "searched", None, "dispatched"),
            (3, "radarr_4k", 3, "movie", "upgrade", "skipped", "on upgrade cooldown (90d)", None),
            (3, "radarr_4k", 4, "movie", "missing", "skipped", "post-release grace (6h)", None),
        ],
    ),
    # Queue-backpressure gate: the whole cycle records a single info row.
    (
        194,
        "scheduled",
        [
            (
                3,
                None,
                -1,
                None,
                None,
                "info",
                "queue backpressure (5/5)",
                "Download queue has 5 items, limit is 5",
            ),
        ],
    ),
    (
        242,
        "run_now",
        [
            (1, "sonarr", 12, "episode", "missing", "searched", None, "dispatched"),
            (1, "sonarr", 13, "episode", "missing", "searched", None, "dispatched"),
        ],
    ),
    (
        317,
        "scheduled",
        [
            (2, None, -1, None, None, "error", None, "Could not reach http://radarr:7878"),
        ],
    ),
    (
        194,
        "scheduled",
        [
            (5, "readarr", 0, "book", "cutoff", "skipped", "on cutoff cooldown (21d)", None),
            (5, "readarr", 1, "book", "cutoff", "skipped", "on cutoff cooldown (21d)", None),
        ],
    ),
]


def build_realistic_cycles(
    pools: dict[str, list[tuple[int, str]]],
    now: datetime,
) -> list[tuple[Any, ...]]:
    """Return ~25 search_log rows spanning the cycles declared above.

    Rows share a ``cycle_id`` within a cycle and a per-row ``timestamp``
    offset from ``now``. Item labels are resolved from ``pools`` so the
    fixture stays aligned with the title JSONs.
    """
    rows: list[tuple[Any, ...]] = []
    for minutes_ago, trigger, specs in _CYCLE_SPECS:
        cycle_id = uuid.uuid4().hex[:12]
        ts_iso = (now - timedelta(minutes=minutes_ago)).isoformat()
        for (
            instance_id,
            pool_key,
            pool_idx,
            item_type,
            search_kind,
            action,
            reason,
            message,
        ) in specs:
            item_id: int | None = None
            item_label: str | None = None
            if pool_key is not None:
                item_id, item_label = pools[pool_key][pool_idx]
            rows.append(
                (
                    instance_id,
                    item_id,
                    item_type,
                    search_kind,
                    cycle_id,
                    trigger,
                    item_label,
                    action,
                    reason,
                    message,
                    ts_iso,
                )
            )
    return rows

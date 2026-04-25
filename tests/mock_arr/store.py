"""In-memory state container for the mock *arr server.

The mock holds one ``AppData`` per *arr type. Each ``AppData`` carries the
seeded record set plus a partition into missing / cutoff-unmet / upgrade
buckets, so the routers can answer ``/wanted/missing``, ``/wanted/cutoff``,
and library scans from the same source of truth without re-randomising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CommandLog:
    """Captures POSTed search commands so tests can assert dispatch behaviour."""

    entries: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PageLog:
    """Records every paginated wanted request so tests can measure distribution.

    Each entry is ``(kind, page, page_size)`` where kind is ``"missing"`` or
    ``"cutoff"``. This is the ground truth for verifying the random search
    algorithm's page-selection fairness.
    """

    entries: list[tuple[str, int, int]] = field(default_factory=list)


@dataclass(slots=True)
class AppData:
    """Per-app mock state.

    ``parents`` holds the optional parent aggregate (series, artist, author).
    Radarr and Whisparr v3 leave it empty because their leaves are top-level.
    ``leaves`` is the searchable record set; the three id sets partition the
    leaves into the three engine passes.
    """

    app_name: str
    app_version: str
    api_prefix: str
    api_version: str
    sort_key_default: str
    sort_direction_default: str
    parents: list[dict[str, Any]]
    leaves: list[dict[str, Any]]
    missing_ids: set[int]
    cutoff_ids: set[int]
    upgrade_ids: set[int]
    command_log: CommandLog = field(default_factory=CommandLog)
    page_log: PageLog = field(default_factory=PageLog)


@dataclass(slots=True)
class MockState:
    """Aggregate state across all six mocked *arr apps."""

    sonarr: AppData
    radarr: AppData
    lidarr: AppData
    readarr: AppData
    whisparr_v2: AppData
    whisparr_v3: AppData

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
    leaves into the three engine passes.  ``queued_ids`` are the leaves the
    mock reports in its download queue; ``queue_detail_requests`` counts
    ``/queue/details`` reads.
    """

    app_name: str
    app_version: str
    api_prefix: str
    api_version: str
    # What an allowlisted build sorts by when it discards a key, and the
    # fallback for a request with no key when ``unsorted_keys`` has no entry.
    sort_key_default: str
    parents: list[dict[str, Any]]
    leaves: list[dict[str, Any]]
    missing_ids: set[int]
    cutoff_ids: set[int]
    upgrade_ids: set[int]
    command_log: CommandLog = field(default_factory=CommandLog)
    page_log: PageLog = field(default_factory=PageLog)
    queued_ids: set[int] = field(default_factory=set)
    queue_detail_requests: int = 0
    # Sort keys that work on every build this repo supports.  The apps
    # disagree by version: Sonarr 3.0.10.1567 answers 500 for
    # ``episodes.airDateUtc`` while 4.x requires that form to be honoured
    # rather than clamped, and Whisparr v2 2.0.0.2151 answers 500 for
    # ``releaseDate`` while 2.2.0 silently accepts it.  Each set is the
    # intersection, so the mock refuses anything not safe everywhere.
    # Current Lidarr and Readarr hand any key to SQLite, so their sets
    # are the columns this repo needs plus a few real ones; widen them
    # when a client legitimately needs another column.
    sort_keys: frozenset[str] = frozenset()
    # The API-layer allowlist of the newest builds (Sonarr 4.0.9.2421+,
    # Whisparr v2 2.2.0+), compared case-insensitively.  A key that passes
    # ``sort_keys`` but is not listed here is swapped for
    # ``sort_key_default``, and the envelope reports the swapped key.
    sort_key_allowlist: frozenset[str] = frozenset()
    # Lower-cased sort key -> leaf field the mock sorts by.  A key with no
    # entry sorts every row as NULL, which leaves them in id order.
    sort_fields: dict[str, str] = field(default_factory=dict)
    # Effective key per kind when the request carries no sortKey; ``None``
    # answers 500, as Sonarr 3.0.10.1567's cutoff endpoint does.
    unsorted_keys: dict[str, str | None] = field(default_factory=dict)
    sort_key_table: str = ""
    # Lidarr upper-cases the first letter of the column in its error text;
    # Readarr does not.  Measured: Lidarr 3.1.0.4875 answers
    # ``no such column: Albums.TotalGarbageXyz`` for ``totalGarbageXyz``.
    sort_key_error_capitalises: bool = False


@dataclass(slots=True)
class MockState:
    """Aggregate state across all six mocked *arr apps."""

    sonarr: AppData
    radarr: AppData
    lidarr: AppData
    readarr: AppData
    whisparr_v2: AppData
    whisparr_v3: AppData

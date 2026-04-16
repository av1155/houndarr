"""Allowed-time-window parsing and evaluation for the search engine.

An operator can restrict searches on a per-instance basis to one or more
wall-clock time-of-day windows, e.g. ``09:00-23:00`` or
``09:00-12:00,18:00-22:00``.  Outside any configured window, the search
loop's gate writes a single ``info`` row and returns 0 for the cycle.

Semantics
---------
* Format: ``HH:MM-HH:MM`` per range; comma-separated for multiple ranges.
* Start is inclusive, end is exclusive: ``09:00-12:00`` allows searches
  from ``09:00:00`` up to but not including ``12:00:00``.
* Windows that span midnight are supported by putting a later start
  before an earlier end, e.g. ``22:00-06:00``.
* An empty or whitespace-only string means *always allowed* (no gate).
"""

from __future__ import annotations

import re
from datetime import time

# Maximum number of ranges a single spec may contain.  Protects against
# pathological inputs and keeps the DB column well under 300 chars.
_MAX_RANGES = 24

_RANGE_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


def _to_minutes(t: time) -> int:
    """Return *t* as minutes since 00:00 (ignoring seconds/microseconds)."""
    return t.hour * 60 + t.minute


def parse_time_window(spec: str) -> list[tuple[time, time]]:
    """Parse an allowed-time-window *spec* into a list of ``(start, end)`` tuples.

    Raises:
        ValueError: With an operator-facing message if the spec is malformed.

    Returns:
        A list of ``(start, end)`` tuples.  Empty list means no window is
        configured (always allowed).  The list is returned in input order;
        overlapping or duplicate ranges are not merged.
    """
    if spec is None:
        return []

    stripped = spec.strip()
    if not stripped:
        return []

    pieces = [p.strip() for p in stripped.split(",")]
    if len(pieces) > _MAX_RANGES:
        raise ValueError(f"Too many time ranges (max {_MAX_RANGES}).")

    ranges: list[tuple[time, time]] = []
    for piece in pieces:
        if not piece:
            raise ValueError("Empty time range in allowed-window spec.")

        match = _RANGE_RE.fullmatch(piece)
        if match is None:
            raise ValueError(f"Invalid time range '{piece}'. Use HH:MM-HH:MM (e.g. 09:00-23:00).")

        sh, sm, eh, em = (int(x) for x in match.groups())
        if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
            raise ValueError(
                f"Out-of-range hour or minute in '{piece}'. Hours 00-23, minutes 00-59."
            )

        start = time(sh, sm)
        end = time(eh, em)
        if _to_minutes(start) == _to_minutes(end):
            raise ValueError(f"Time range '{piece}' has no duration (start equals end).")

        ranges.append((start, end))

    return ranges


def is_within_window(now_time: time, ranges: list[tuple[time, time]]) -> bool:
    """Return whether *now_time* falls inside any of the configured *ranges*.

    An empty *ranges* list means no window is configured and the function
    returns ``True`` (always allowed).  Ranges where ``start > end`` wrap
    around midnight (e.g. ``22:00-06:00`` matches both 23:00 and 05:00).
    Start is inclusive and end is exclusive.
    """
    if not ranges:
        return True

    now_minutes = _to_minutes(now_time)
    for start, end in ranges:
        start_m = _to_minutes(start)
        end_m = _to_minutes(end)
        if start_m < end_m:
            if start_m <= now_minutes < end_m:
                return True
        else:
            # Wraparound: allowed region is [start, 1440) ∪ [0, end)
            if now_minutes >= start_m or now_minutes < end_m:
                return True

    return False


def format_ranges(ranges: list[tuple[time, time]]) -> str:
    """Render *ranges* back to a canonical ``HH:MM-HH:MM,...`` string.

    Used to include the configured window in operator-facing log messages.
    """
    return ",".join(f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in ranges)

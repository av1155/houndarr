"""Parse CHANGELOG.md and decide when the "What's new" modal should render.

The parser expects the strict subset of Keep a Changelog 1.1.0 that
``.github/workflows/version-check.yml`` enforces on every release PR:

- ``## [X.Y.Z] - YYYY-MM-DD`` opens a versioned block.
- ``### Added|Changed|Deprecated|Removed|Fixed|Security`` (or any other
  ``###`` header, rendered permissively) opens a section within a block.
- ``- `` starts a bullet (may continue onto wrapped lines).
- ``---`` closes the block.

The ``## [Unreleased]`` block at the top of CHANGELOG.md is intentionally
skipped: ``_VERSION_HEADING`` requires a strict ``X.Y.Z`` plus an ISO date,
so the heading does not match and bullets accumulated for the next release
stay invisible to the in-app modal until ``/bump`` promotes them to a
versioned block.

Unexpected content is skipped rather than raising; a malformed file
degrades to an empty changelog so the modal short-circuits instead of
crashing the app on startup.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# services/changelog.py → src/houndarr/services/ → src/houndarr/ → src/ → <root>
CHANGELOG_PATH: Path = Path(__file__).resolve().parent.parent.parent.parent / "CHANGELOG.md"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ReleaseSection:
    """A single ``### Heading`` section within a release block."""

    heading: str
    bullets: list[str]


@dataclass(frozen=True, slots=True)
class ReleaseEntry:
    """One ``## [X.Y.Z] - YYYY-MM-DD`` release block."""

    version: str
    version_tuple: tuple[int, int, int]
    date: str
    sections: list[ReleaseSection]


# ---------------------------------------------------------------------------
# Regexes (module-level so the compile cost is paid once)
# ---------------------------------------------------------------------------
_VERSION_HEADING = re.compile(r"^## \[(?P<ver>\d+\.\d+\.\d+)\] - (?P<date>\d{4}-\d{2}-\d{2})\s*$")
_SECTION_HEADING = re.compile(r"^### (?P<heading>\S.*\S|\S)\s*$")
_BULLET_START = re.compile(r"^- (?P<text>.+)$")
_BLOCK_TERMINATOR = re.compile(r"^---\s*$")


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------
def _parse_version(s: str | None) -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` or ``None`` for any non-strict-semver input.

    Houndarr versions are enforced as ``^\\d+\\.\\d+\\.\\d+$`` by
    ``version-check.yml`` on every release PR, so the universe of valid
    values is closed.  Anything else (``"dev"``, ``"1.0.0rc1"``, corrupted
    settings rows) returns ``None`` and is treated as "unknown".
    """
    if not s:
        return None
    try:
        parts = s.strip().split(".")
        if len(parts) != 3:
            return None
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _parse_changelog(path: Path) -> list[ReleaseEntry]:
    """Parse *path* and return release entries in descending version order.

    Returns ``[]`` if the file is missing or contains no parseable blocks.
    Emits a single WARNING log when the file is missing entirely.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("CHANGELOG.md not found at %s", path)
        return []
    except OSError:
        logger.warning("CHANGELOG.md unreadable at %s", path, exc_info=True)
        return []

    entries: list[ReleaseEntry] = []
    current_version: str | None = None
    current_tuple: tuple[int, int, int] | None = None
    current_date: str | None = None
    current_sections: list[ReleaseSection] = []
    current_heading: str | None = None
    current_bullets: list[str] = []

    def _flush_section() -> None:
        if current_heading is not None:
            current_sections.append(
                ReleaseSection(heading=current_heading, bullets=list(current_bullets))
            )

    def _flush_block() -> None:
        if current_version is None or current_tuple is None or current_date is None:
            return
        entries.append(
            ReleaseEntry(
                version=current_version,
                version_tuple=current_tuple,
                date=current_date,
                sections=list(current_sections),
            )
        )

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        version_match = _VERSION_HEADING.match(line)
        if version_match is not None:
            _flush_section()
            _flush_block()
            current_version = version_match.group("ver")
            current_tuple = _parse_version(current_version)
            current_date = version_match.group("date")
            current_sections = []
            current_heading = None
            current_bullets = []
            continue

        if current_version is None:
            continue

        if _BLOCK_TERMINATOR.match(line):
            _flush_section()
            _flush_block()
            current_version = None
            current_tuple = None
            current_date = None
            current_sections = []
            current_heading = None
            current_bullets = []
            continue

        section_match = _SECTION_HEADING.match(line)
        if section_match is not None:
            _flush_section()
            current_heading = section_match.group("heading")
            current_bullets = []
            continue

        bullet_match = _BULLET_START.match(line)
        if bullet_match is not None and current_heading is not None:
            current_bullets.append(bullet_match.group("text"))
            continue

        # Continuation of a wrapped bullet: indented line following a bullet.
        if current_bullets and line.startswith("  "):
            current_bullets[-1] = f"{current_bullets[-1]} {line.strip()}"

    # End-of-file: flush any unterminated trailing block (defensive).
    _flush_section()
    _flush_block()

    entries.sort(key=lambda e: e.version_tuple, reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Lazy singleton cache
# ---------------------------------------------------------------------------
_cache: list[ReleaseEntry] | None = None


def get_changelog() -> list[ReleaseEntry]:
    """Return parsed release entries, cached for the lifetime of the process.

    Matches the lazy-initialisation pattern used by ``get_templates()`` in
    ``routes/pages.py`` so parse failures surface on the route that needs
    them, not during app startup.
    """
    global _cache  # noqa: PLW0603
    if _cache is None:
        _cache = _parse_changelog(CHANGELOG_PATH)
    return _cache


def _reset_changelog_cache() -> None:
    """Clear the cache. Test-only hook."""
    global _cache  # noqa: PLW0603
    _cache = None


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------
def releases_between(*, last_seen: str | None, running: str | None) -> list[ReleaseEntry]:
    """Return release entries with ``last_seen < version <= running``, newest first.

    - If *running* is unparseable, returns ``[]`` (we cannot know what to
      show and must not guess).
    - If *last_seen* is absent or unparseable, returns only the single entry
      matching *running* (the pre-feature-upgrade case: we cannot reconstruct
      the user's prior version, so we show a one-time marker of the current
      release and let the dismiss handler seed the stored value).
    """
    running_tuple = _parse_version(running)
    if running_tuple is None:
        return []

    entries = get_changelog()

    last_seen_tuple = _parse_version(last_seen)
    if last_seen_tuple is None:
        for entry in entries:
            if entry.version_tuple == running_tuple:
                return [entry]
        return []

    return [entry for entry in entries if last_seen_tuple < entry.version_tuple <= running_tuple]


def should_show(*, last_seen: str | None, running: str | None, disabled: bool) -> bool:
    """Return True iff the modal should auto-open on this dashboard load."""
    if disabled:
        return False
    running_tuple = _parse_version(running)
    if running_tuple is None:
        return False
    last_seen_tuple = _parse_version(last_seen)
    if last_seen_tuple is None:
        return True
    return running_tuple > last_seen_tuple

"""Tests for changelog parsing, version comparison, and show-decision logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from houndarr.services import changelog as cl

# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.8.0", (1, 8, 0)),
        ("0.0.0", (0, 0, 0)),
        ("10.20.30", (10, 20, 30)),
        ("  1.2.3  ", (1, 2, 3)),
    ],
)
def test_parse_version_valid(raw: str, expected: tuple[int, int, int]) -> None:
    assert cl._parse_version(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "abc",
        "1.2",
        "1.2.3.4",
        "1.2.3-rc1",
        "1.2.3+build",
        "v1.2.3",
        "1.2.x",
    ],
)
def test_parse_version_invalid(raw: str | None) -> None:
    assert cl._parse_version(raw) is None


# ---------------------------------------------------------------------------
# _parse_changelog
# ---------------------------------------------------------------------------


_SAMPLE_CHANGELOG = """# Changelog

All notable changes to this project will be documented in this file.

## [1.8.0] - 2026-04-16

### Added

- New feature. (#1)
- Another feature with `code` and [link](https://example.com).

### Fixed

- A bug. (#2)

---

## [1.7.0] - 2026-04-04

### Added

- Whisparr v3 support. (#338)

### Fixed

- Whisparr v2 `releaseDate` parsing. (#339)

---

## [1.6.0] - 2026-03-21

### Changed

- Inserted 3-second delay between searches. (#272)

---
"""


def _write_changelog(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_changelog_valid(tmp_path: Path) -> None:
    path = _write_changelog(tmp_path, _SAMPLE_CHANGELOG)
    entries = cl._parse_changelog(path)

    assert len(entries) == 3
    assert [e.version for e in entries] == ["1.8.0", "1.7.0", "1.6.0"]
    assert [e.version_tuple for e in entries] == [(1, 8, 0), (1, 7, 0), (1, 6, 0)]
    assert entries[0].date == "2026-04-16"

    sections = {s.heading: s.bullets for s in entries[0].sections}
    assert sections["Added"] == [
        "New feature. (#1)",
        "Another feature with `code` and [link](https://example.com).",
    ]
    assert sections["Fixed"] == ["A bug. (#2)"]


def test_parse_changelog_missing_file(tmp_path: Path) -> None:
    entries = cl._parse_changelog(tmp_path / "does-not-exist.md")
    assert entries == []


def test_parse_changelog_empty_file(tmp_path: Path) -> None:
    path = _write_changelog(tmp_path, "# Changelog\n\nNo entries yet.\n")
    assert cl._parse_changelog(path) == []


def test_parse_changelog_skips_malformed_heading(tmp_path: Path) -> None:
    content = """## [1.8.0] 2026-04-16

### Added

- Should be skipped; missing dash separator in the heading.

---

## [1.7.0] - 2026-04-04

### Added

- Valid entry.

---
"""
    entries = cl._parse_changelog(_write_changelog(tmp_path, content))
    assert [e.version for e in entries] == ["1.7.0"]


def test_parse_changelog_permissive_section_headers(tmp_path: Path) -> None:
    content = """## [2.0.0] - 2026-05-01

### Security

- Patched a CVE. (#999)

### Deprecated

- Old flag removed in next major.

---
"""
    entries = cl._parse_changelog(_write_changelog(tmp_path, content))
    assert len(entries) == 1
    headings = [s.heading for s in entries[0].sections]
    assert headings == ["Security", "Deprecated"]


def test_parse_changelog_wrapped_bullets(tmp_path: Path) -> None:
    content = """## [1.8.0] - 2026-04-16

### Added

- First bullet that continues
  onto a second wrapped line.
- Second bullet.

---
"""
    entries = cl._parse_changelog(_write_changelog(tmp_path, content))
    assert entries[0].sections[0].bullets == [
        "First bullet that continues onto a second wrapped line.",
        "Second bullet.",
    ]


def test_parse_changelog_unterminated_trailing_block(tmp_path: Path) -> None:
    content = """## [1.8.0] - 2026-04-16

### Added

- Bullet.
"""
    entries = cl._parse_changelog(_write_changelog(tmp_path, content))
    assert len(entries) == 1
    assert entries[0].version == "1.8.0"


# ---------------------------------------------------------------------------
# Lazy singleton cache
# ---------------------------------------------------------------------------


def test_get_changelog_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_changelog(tmp_path, _SAMPLE_CHANGELOG)
    monkeypatch.setattr(cl, "CHANGELOG_PATH", path)
    cl._reset_changelog_cache()

    first = cl.get_changelog()
    second = cl.get_changelog()
    assert first is second


def test_reset_changelog_cache_forces_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_changelog(tmp_path, _SAMPLE_CHANGELOG)
    monkeypatch.setattr(cl, "CHANGELOG_PATH", path)
    cl._reset_changelog_cache()

    first = cl.get_changelog()
    cl._reset_changelog_cache()
    second = cl.get_changelog()
    assert first is not second
    assert [e.version for e in first] == [e.version for e in second]


# ---------------------------------------------------------------------------
# releases_between
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_changelog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = _write_changelog(tmp_path, _SAMPLE_CHANGELOG)
    monkeypatch.setattr(cl, "CHANGELOG_PATH", path)
    cl._reset_changelog_cache()
    return path


def test_releases_between_spans_multiple(sample_changelog: Path) -> None:
    entries = cl.releases_between(last_seen="1.6.0", running="1.8.0")
    assert [e.version for e in entries] == ["1.8.0", "1.7.0"]


def test_releases_between_single_match(sample_changelog: Path) -> None:
    entries = cl.releases_between(last_seen="1.7.0", running="1.8.0")
    assert [e.version for e in entries] == ["1.8.0"]


def test_releases_between_none_when_current(sample_changelog: Path) -> None:
    assert cl.releases_between(last_seen="1.8.0", running="1.8.0") == []


def test_releases_between_downgrade(sample_changelog: Path) -> None:
    assert cl.releases_between(last_seen="1.8.0", running="1.6.0") == []


def test_releases_between_unparseable_running(sample_changelog: Path) -> None:
    assert cl.releases_between(last_seen="1.6.0", running=None) == []
    assert cl.releases_between(last_seen="1.6.0", running="bogus") == []


def test_releases_between_absent_last_seen_returns_current_only(
    sample_changelog: Path,
) -> None:
    entries = cl.releases_between(last_seen=None, running="1.8.0")
    assert [e.version for e in entries] == ["1.8.0"]


def test_releases_between_unparseable_last_seen_returns_current_only(
    sample_changelog: Path,
) -> None:
    entries = cl.releases_between(last_seen="corrupted", running="1.8.0")
    assert [e.version for e in entries] == ["1.8.0"]


def test_releases_between_absent_last_seen_no_matching_entry(
    sample_changelog: Path,
) -> None:
    """Running version has no changelog entry (e.g. dev build, missing block)."""
    assert cl.releases_between(last_seen=None, running="9.9.9") == []


# ---------------------------------------------------------------------------
# should_show
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("last_seen", "running", "disabled", "expected"),
    [
        # Happy paths
        ("1.6.0", "1.8.0", False, True),  # upgrade → show
        (None, "1.8.0", False, True),  # never seen → show
        # Disabled trumps everything
        ("1.6.0", "1.8.0", True, False),
        (None, "1.8.0", True, False),
        # Current version already seen
        ("1.8.0", "1.8.0", False, False),
        # Downgrade
        ("1.8.0", "1.6.0", False, False),
        # Unparseable running
        ("1.6.0", None, False, False),
        ("1.6.0", "bogus", False, False),
        # Unparseable last_seen → treat as never seen
        ("corrupted", "1.8.0", False, True),
    ],
)
def test_should_show_truth_table(
    last_seen: str | None, running: str | None, disabled: bool, expected: bool
) -> None:
    assert cl.should_show(last_seen=last_seen, running=running, disabled=disabled) == expected

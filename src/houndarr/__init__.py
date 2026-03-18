"""Houndarr — focused, self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and Whisparr."""

from pathlib import Path

# Read version from the canonical VERSION file at repo root
_VERSION_FILE = Path(__file__).parent.parent.parent / "VERSION"
try:
    __version__ = _VERSION_FILE.read_text(encoding="utf-8").strip()
except FileNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]

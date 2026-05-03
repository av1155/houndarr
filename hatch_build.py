"""Hatchling metadata hook: read project version from the VERSION file.

The VERSION file remains the single source of truth for releases (consumed by
release.yml, chart.yml, version-check.yml, and the /bump workflow). This hook
keeps that contract while letting hatchling drive the build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.metadata.plugin.interface import MetadataHookInterface


class VersionFromFileHook(MetadataHookInterface):
    """Read the project version from a plain-text VERSION file at repo root."""

    PLUGIN_NAME = "version-from-file"

    def update(self, metadata: dict[str, Any]) -> None:
        version_path = Path(self.root) / "VERSION"
        if not version_path.is_file():
            raise FileNotFoundError(f"VERSION file not found at {version_path}")
        version = version_path.read_text(encoding="utf-8").strip()
        if not version:
            raise ValueError(f"VERSION file at {version_path} is empty")
        metadata["version"] = version

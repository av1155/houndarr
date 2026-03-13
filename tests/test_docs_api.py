"""Sanity checks for local API reference snapshots."""

from __future__ import annotations

import json
from pathlib import Path


def _load_openapi(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def test_api_snapshot_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "api" / "sonarr_openapi.json").is_file()
    assert (root / "docs" / "api" / "radarr_openapi.json").is_file()
    assert (root / "docs" / "api-context.md").is_file()


def test_sonarr_snapshot_contains_houndarr_endpoints() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = _load_openapi(root / "docs" / "api" / "sonarr_openapi.json")
    paths = spec.get("paths")
    assert isinstance(paths, dict)
    assert "/api/v3/system/status" in paths
    assert "/api/v3/wanted/missing" in paths
    assert "/api/v3/command" in paths


def test_radarr_snapshot_contains_houndarr_endpoints() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = _load_openapi(root / "docs" / "api" / "radarr_openapi.json")
    paths = spec.get("paths")
    assert isinstance(paths, dict)
    assert "/api/v3/system/status" in paths
    assert "/api/v3/wanted/missing" in paths
    assert "/api/v3/command" in paths

"""Sanity checks for local API reference snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SONARR_SNAPSHOT_SHA256 = "3fd4c4f4385b1043c3568bd3b37fa6c3c0161135072962dffb611f4ff270e2b7"
RADARR_SNAPSHOT_SHA256 = "95ea9062485118d6a8abed8250b9bfbf94e4de0f55e9c5611da6805864f9a26e"


def _load_openapi(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_api_snapshot_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "api" / "sonarr_openapi.json").is_file()
    assert (root / "docs" / "api" / "radarr_openapi.json").is_file()
    assert (root / "docs" / "api-context.md").is_file()


def test_api_snapshot_hashes_match_expected() -> None:
    root = Path(__file__).resolve().parents[1]
    sonarr = root / "docs" / "api" / "sonarr_openapi.json"
    radarr = root / "docs" / "api" / "radarr_openapi.json"
    assert _sha256(sonarr) == SONARR_SNAPSHOT_SHA256
    assert _sha256(radarr) == RADARR_SNAPSHOT_SHA256


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

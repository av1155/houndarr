"""Sanity checks for local API reference snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

SONARR_SNAPSHOT_SHA256 = "3fd4c4f4385b1043c3568bd3b37fa6c3c0161135072962dffb611f4ff270e2b7"
RADARR_SNAPSHOT_SHA256 = "95ea9062485118d6a8abed8250b9bfbf94e4de0f55e9c5611da6805864f9a26e"
WHISPARR_V2_SNAPSHOT_SHA256 = "e16d5052c6da3fdb9c54739890412c340c4b485c0a1b53af15f5a6ac837bb0a2"
WHISPARR_V3_SNAPSHOT_SHA256 = "349b0dd4abdf92a569e03b834aa40e4061856ea401be89232d3ed6c2cd2e1250"
LIDARR_SNAPSHOT_SHA256 = "4ae9e79e9662898ed4704ce80f091161587a9f0d664f9e518b11a038616e491f"
READARR_SNAPSHOT_SHA256 = "67816240e90b225d897fa713bd3f842f90d26d4c73cb522d8a8b59d32391cad8"


def _load_openapi(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_api_snapshot_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "api" / "sonarr_openapi.json").is_file()
    assert (root / "docs" / "api" / "radarr_openapi.json").is_file()
    assert (root / "docs" / "api" / "whisparr_v2_openapi.json").is_file()
    assert (root / "docs" / "api" / "whisparr_v3_openapi.json").is_file()
    assert (root / "docs" / "api" / "lidarr_openapi.json").is_file()
    assert (root / "docs" / "api" / "readarr_openapi.json").is_file()
    assert (root / "docs" / "api" / "README.md").is_file()


def test_api_snapshot_hashes_match_expected() -> None:
    root = Path(__file__).resolve().parents[1]
    sonarr = root / "docs" / "api" / "sonarr_openapi.json"
    radarr = root / "docs" / "api" / "radarr_openapi.json"
    whisparr_v2 = root / "docs" / "api" / "whisparr_v2_openapi.json"
    whisparr_v3 = root / "docs" / "api" / "whisparr_v3_openapi.json"
    lidarr = root / "docs" / "api" / "lidarr_openapi.json"
    readarr = root / "docs" / "api" / "readarr_openapi.json"
    assert _sha256(sonarr) == SONARR_SNAPSHOT_SHA256
    assert _sha256(radarr) == RADARR_SNAPSHOT_SHA256
    assert _sha256(whisparr_v2) == WHISPARR_V2_SNAPSHOT_SHA256
    assert _sha256(whisparr_v3) == WHISPARR_V3_SNAPSHOT_SHA256
    assert _sha256(lidarr) == LIDARR_SNAPSHOT_SHA256
    assert _sha256(readarr) == READARR_SNAPSHOT_SHA256


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


def test_whisparr_v2_snapshot_contains_expected_endpoints() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = _load_openapi(root / "docs" / "api" / "whisparr_v2_openapi.json")
    paths = spec.get("paths")
    assert isinstance(paths, dict)
    assert "/api/v3/system/status" in paths
    assert "/api/v3/wanted/missing" in paths
    assert "/api/v3/command" in paths


def test_whisparr_v3_snapshot_contains_expected_endpoints() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = _load_openapi(root / "docs" / "api" / "whisparr_v3_openapi.json")
    paths = spec.get("paths")
    assert isinstance(paths, dict)
    assert "/api/v3/system/status" in paths
    assert "/api/v3/movie" in paths
    assert "/api/v3/command" in paths


def test_lidarr_snapshot_contains_expected_endpoints() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = _load_openapi(root / "docs" / "api" / "lidarr_openapi.json")
    paths = spec.get("paths")
    assert isinstance(paths, dict)
    assert "/api/v1/system/status" in paths
    assert "/api/v1/wanted/missing" in paths
    assert "/api/v1/command" in paths


def test_readarr_snapshot_contains_expected_endpoints() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = _load_openapi(root / "docs" / "api" / "readarr_openapi.json")
    paths = spec.get("paths")
    assert isinstance(paths, dict)
    assert "/api/v1/system/status" in paths
    assert "/api/v1/wanted/missing" in paths
    assert "/api/v1/command" in paths


_QUEUE_LEAF_FIELDS = frozenset({"episodeId", "movieId", "albumId", "bookId"})


@pytest.mark.parametrize(
    ("snapshot", "details_path", "leaf_field", "embed_param"),
    [
        ("sonarr_openapi.json", "/api/v3/queue/details", "episodeId", None),
        ("radarr_openapi.json", "/api/v3/queue/details", "movieId", None),
        ("whisparr_v2_openapi.json", "/api/v3/queue/details", "episodeId", None),
        ("whisparr_v3_openapi.json", "/api/v3/queue/details", "movieId", None),
        ("lidarr_openapi.json", "/api/v1/queue/details", "albumId", "includeAlbum"),
        ("readarr_openapi.json", "/api/v1/queue/details", "bookId", "includeBook"),
    ],
)
def test_queue_details_contract(
    snapshot: str,
    details_path: str,
    leaf_field: str,
    embed_param: str | None,
) -> None:
    """Pin what the per-item download-queue check reads from ``/queue/details``.

    ``QueueRecord.item_id`` takes whichever leaf id is present, which is only
    safe while each app's ``QueueResource`` carries exactly one of them.  The
    embed flag Houndarr sends as ``false`` must stay the only include
    parameter that defaults to ``true``.
    """
    root = Path(__file__).resolve().parents[1]
    spec: dict[str, Any] = _load_openapi(root / "docs" / "api" / snapshot)
    details = spec["paths"][details_path]["get"]
    response_schema = details["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["type"] == "array"
    queue_resource = spec["components"]["schemas"]["QueueResource"]["properties"]
    assert set(queue_resource) & _QUEUE_LEAF_FIELDS == {leaf_field}
    true_defaults = {
        param["name"]
        for param in details.get("parameters", [])
        if param.get("schema", {}).get("default") is True
    }
    assert true_defaults == ({embed_param} if embed_param else set())

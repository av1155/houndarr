"""Pin that the mock rejects a sortKey the real app would not accept.

Two wrong sort keys shipped and survived a green suite, because the mock
echoed whatever it was handed. Real \\*arr behaviour splits two ways, both
measured against live instances:

- Sonarr, Radarr and Whisparr v2 gained an API-layer allowlist in Sept
  2024. An unlisted key is silently swapped for the app's own default, so
  the request succeeds and sorts by the wrong column. Sonarr 4.0.20.3014
  answers 200 and reports ``episodes.airDateUtc`` for a made-up key.
- Lidarr and Readarr never got one, so the key reaches SQLite. Lidarr
  3.1.0.4875 answers 500 with ``no such column: Albums.<key>``.

The client keys are asserted against the same sets, so a client that
starts sending a key its app would discard fails here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from tests.mock_arr.server import create_app

_APPS: list[tuple[str, str, str]] = [
    ("sonarr", "/sonarr/api/v3", "episodes.airDateUtc"),
    ("radarr", "/radarr/api/v3", "movieMetadata.sortTitle"),
    ("whisparr_v2", "/whisparr_v2/api/v3", "episodes.airDateUtc"),
]

_STRICT_APPS: list[tuple[str, str, str]] = [
    ("lidarr", "/lidarr/api/v1", "Albums"),
    ("readarr", "/readarr/api/v1", "Books"),
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize(
    ("name", "prefix", "expected_default"),
    _APPS,
    ids=[name for name, *_ in _APPS],
)
def test_an_allowlist_app_swaps_an_unknown_key_for_its_default(
    client: TestClient, name: str, prefix: str, expected_default: str
) -> None:
    """The failure that hid the Radarr bug: a 200 that sorted by the wrong column."""
    resp = client.get(f"{prefix}/wanted/missing", params={"sortKey": "totalGarbageXyz"})
    assert resp.status_code == 200
    assert resp.json()["sortKey"] == expected_default


@pytest.mark.parametrize(
    ("name", "prefix", "table"),
    _STRICT_APPS,
    ids=[name for name, *_ in _STRICT_APPS],
)
def test_an_app_without_an_allowlist_answers_500(
    client: TestClient, name: str, prefix: str, table: str
) -> None:
    """The failure that broke Whisparr v2 and old Radarr outright."""
    resp = client.get(f"{prefix}/wanted/missing", params={"sortKey": "totalGarbageXyz"})
    assert resp.status_code == 500
    assert f"no such column: {table}.totalGarbageXyz" in resp.text


@pytest.mark.parametrize(
    ("name", "prefix", "sort_key"),
    [
        ("sonarr", "/sonarr/api/v3", SonarrClient._WANTED_SORT_KEY),
        ("radarr", "/radarr/api/v3", RadarrClient._WANTED_SORT_KEY),
        ("whisparr_v2", "/whisparr_v2/api/v3", WhisparrV2Client._WANTED_SORT_KEY),
        ("lidarr", "/lidarr/api/v1", LidarrClient._WANTED_SORT_KEY),
        ("readarr", "/readarr/api/v1", ReadarrClient._WANTED_SORT_KEY),
    ],
    ids=["sonarr", "radarr", "whisparr_v2", "lidarr", "readarr"],
)
def test_every_client_sends_a_key_its_app_accepts(
    client: TestClient, name: str, prefix: str, sort_key: str
) -> None:
    """Radarr's `inCinemas` failed this for 18 months; nothing caught it."""
    for kind in ("missing", "cutoff"):
        resp = client.get(f"{prefix}/wanted/{kind}", params={"sortKey": sort_key})
        assert resp.status_code == 200, f"{name} {kind}: {resp.text[:120]}"
        assert resp.json()["sortKey"] == sort_key, (
            f"{name} {kind}: the app discarded {sort_key!r} and sorted by "
            f"{resp.json()['sortKey']!r} instead"
        )

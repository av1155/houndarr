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

_ALL_APPS: list[tuple[str, str, str]] = [
    ("sonarr", "/sonarr/api/v3", "Episodes"),
    ("radarr", "/radarr/api/v3", "Movies"),
    ("whisparr_v2", "/whisparr_v2/api/v3", "Episodes"),
    ("lidarr", "/lidarr/api/v1", "Albums"),
    ("readarr", "/readarr/api/v1", "Books"),
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize(
    ("name", "prefix", "table"),
    _ALL_APPS,
    ids=[name for name, *_ in _ALL_APPS],
)
def test_a_key_the_app_would_not_accept_is_refused(
    client: TestClient, name: str, prefix: str, table: str
) -> None:
    """Older builds answer 500 for a key that is not a real column."""
    resp = client.get(f"{prefix}/wanted/missing", params={"sortKey": "totalGarbageXyz"})
    assert resp.status_code == 500
    # Lidarr upper-cases the column in its message; the others do not.
    expected = "TotalGarbageXyz" if name == "lidarr" else "totalGarbageXyz"
    assert f"no such column: {table}.{expected}" in resp.text


@pytest.mark.parametrize(
    ("name", "prefix", "sort_key", "expected_column"),
    [
        # Sonarr keeps the bare form: 3.0.10.1567 answers 500 for
        # "episodes.airDateUtc" because `episodes` is not a property of its
        # Episode model, while 4.x clamps the bare form to that same column.
        ("sonarr", "/sonarr/api/v3", SonarrClient._WANTED_SORT_KEY, "airDateUtc"),
        ("radarr", "/radarr/api/v3", RadarrClient._WANTED_SORT_KEY, "movieMetadata.inCinemas"),
        # Whisparr v2 takes the qualified form: 2.0.0.2151 resolves both to the
        # same column and 2.2.0 honours only this one.
        (
            "whisparr_v2",
            "/whisparr_v2/api/v3",
            WhisparrV2Client._WANTED_SORT_KEY,
            "episodes.airDateUtc",
        ),
        ("lidarr", "/lidarr/api/v1", LidarrClient._WANTED_SORT_KEY, "releaseDate"),
        ("readarr", "/readarr/api/v1", ReadarrClient._WANTED_SORT_KEY, "releaseDate"),
    ],
    ids=["sonarr", "radarr", "whisparr_v2", "lidarr", "readarr"],
)
def test_every_client_sorts_by_its_release_date_column(
    client: TestClient, name: str, prefix: str, sort_key: str, expected_column: str
) -> None:
    """Radarr's `inCinemas` failed this for eighteen months; nothing caught it.

    The contract is the column the app ends up sorting by, not the string it
    echoes.  A key the app discards still fails here, because the column it
    falls back to is not the one the pass wants.
    """
    for kind in ("missing", "cutoff"):
        resp = client.get(f"{prefix}/wanted/{kind}", params={"sortKey": sort_key})
        assert resp.status_code == 200, f"{name} {kind}: {resp.text[:120]}"
        assert resp.json()["sortKey"] == expected_column, (
            f"{name} {kind}: sent {sort_key!r} and the app sorted by "
            f"{resp.json()['sortKey']!r} instead of {expected_column!r}"
        )

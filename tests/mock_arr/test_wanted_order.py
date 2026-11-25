"""Pin that the mock orders ``/wanted`` pages the way the real apps do.

Until the mock sorted, a request without ``sortKey`` or ``sortDirection``
looked exactly like one that sent them: every router filled in its own
ascending default, and seeded dates rose with id, so storage order was
already oldest-first.  A client that dropped the sort params passed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from tests.mock_arr._common import sort_records
from tests.mock_arr.server import SeedConfig, build_state, create_app

# (app, prefix, the client's key, the leaf field that key sorts by)
_PAGED_APPS: list[tuple[str, str, str, str]] = [
    ("sonarr", "/sonarr/api/v3", SonarrClient._WANTED_SORT_KEY, "airDateUtc"),
    ("radarr", "/radarr/api/v3", RadarrClient._WANTED_SORT_KEY, "inCinemas"),
    ("whisparr_v2", "/whisparr_v2/api/v3", WhisparrV2Client._WANTED_SORT_KEY, "releaseDate"),
    ("lidarr", "/lidarr/api/v1", LidarrClient._WANTED_SORT_KEY, "releaseDate"),
    ("readarr", "/readarr/api/v1", ReadarrClient._WANTED_SORT_KEY, "releaseDate"),
]
_IDS = [name for name, *_ in _PAGED_APPS]
_PAGE_SIZE = 37


@pytest.fixture(scope="module")
def shuffled() -> TestClient:
    return TestClient(create_app(SeedConfig(shuffle_dates=True)))


def _walk(client: TestClient, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = client.get(url, params={**params, "page": page, "pageSize": _PAGE_SIZE})
        assert resp.status_code == 200, resp.text[:200]
        body = resp.json()
        records.extend(body["records"])
        if page * _PAGE_SIZE >= body["totalRecords"]:
            return records
        page += 1


@pytest.mark.parametrize(("name", "prefix", "sort_key", "field"), _PAGED_APPS, ids=_IDS)
@pytest.mark.parametrize("kind", ["missing", "cutoff"])
def test_ascending_walk_is_oldest_first_when_storage_is_not(
    shuffled: TestClient, name: str, prefix: str, sort_key: str, field: str, kind: str
) -> None:
    url = f"{prefix}/wanted/{kind}"
    records = _walk(shuffled, url, {"sortKey": sort_key, "sortDirection": "ascending"})
    ids = [record["id"] for record in records]
    dates = [record[field] for record in records]
    assert ids != sorted(ids), "shuffled seed still stores records in date order"
    assert dates == sorted(dates)


@pytest.mark.parametrize(("name", "prefix", "sort_key", "field"), _PAGED_APPS, ids=_IDS)
def test_a_missing_direction_sorts_descending(
    shuffled: TestClient, name: str, prefix: str, sort_key: str, field: str
) -> None:
    resp = shuffled.get(f"{prefix}/wanted/missing", params={"sortKey": sort_key, "pageSize": 500})
    body = resp.json()
    dates = [record[field] for record in body["records"]]
    assert body["sortDirection"] == "descending"
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize(
    ("name", "prefix", "kind", "echoed", "field"),
    [
        ("sonarr", "/sonarr/api/v3", "missing", "episodes.airDateUtc", "airDateUtc"),
        ("radarr", "/radarr/api/v3", "missing", "movieMetadata.sortTitle", "sortTitle"),
        ("radarr", "/radarr/api/v3", "cutoff", "movieMetadata.sortTitle", "sortTitle"),
        ("whisparr_v2", "/whisparr_v2/api/v3", "missing", "episodes.airDateUtc", "releaseDate"),
        ("whisparr_v2", "/whisparr_v2/api/v3", "cutoff", "episodes.airDateUtc", "releaseDate"),
        ("lidarr", "/lidarr/api/v1", "missing", "releaseDate", "releaseDate"),
        ("lidarr", "/lidarr/api/v1", "cutoff", "Albums.Id", "id"),
        ("readarr", "/readarr/api/v1", "missing", "Books.Id", "id"),
        ("readarr", "/readarr/api/v1", "cutoff", "Books.Id", "id"),
    ],
)
def test_no_sort_params_fall_back_to_each_apps_own_order(
    shuffled: TestClient, name: str, prefix: str, kind: str, echoed: str, field: str
) -> None:
    resp = shuffled.get(f"{prefix}/wanted/{kind}", params={"pageSize": 500})
    assert resp.status_code == 200
    body = resp.json()
    values = [record[field] for record in body["records"]]
    assert (body["sortKey"], body["sortDirection"]) == (echoed, "descending")
    assert values == sorted(values, reverse=True)


def test_sonarr_cutoff_without_a_key_fails_like_3_0_10(shuffled: TestClient) -> None:
    resp = shuffled.get("/sonarr/api/v3/wanted/cutoff", params={"sortDirection": "ascending"})
    assert resp.status_code == 500


@pytest.mark.parametrize("prefix", ["/sonarr/api/v3", "/whisparr_v2/api/v3"])
def test_allowlisted_builds_report_the_key_they_swapped_in(
    shuffled: TestClient, prefix: str
) -> None:
    swapped = shuffled.get(f"{prefix}/wanted/cutoff", params={"sortKey": "airDateUtc"})
    kept = shuffled.get(f"{prefix}/wanted/cutoff", params={"sortKey": "series.sortTitle"})
    assert swapped.json()["sortKey"] == "episodes.airDateUtc"
    assert kept.json()["sortKey"] == "series.sortTitle"


def test_nulls_sort_first_ascending_and_last_descending() -> None:
    records = [{"id": 1, "d": "b"}, {"id": 2, "d": None}, {"id": 3, "d": "a"}, {"id": 4, "d": "b"}]
    asc = sort_records(records, field="d", descending=False)
    desc = sort_records(records, field="d", descending=True)
    assert [r["id"] for r in asc] == [2, 3, 1, 4]
    assert [r["id"] for r in desc] == [1, 4, 3, 2]


def test_shuffled_dates_are_seeded_and_only_reorder_existing_dates() -> None:
    plain = build_state(SeedConfig())
    first = build_state(SeedConfig(shuffle_dates=True))
    again = build_state(SeedConfig(shuffle_dates=True))
    for app in ("sonarr", "radarr", "lidarr", "readarr", "whisparr_v2", "whisparr_v3"):
        plain_leaves = getattr(plain, app).leaves
        shuffled_leaves = getattr(first, app).leaves
        assert shuffled_leaves == getattr(again, app).leaves
        assert [leaf["id"] for leaf in shuffled_leaves] == [leaf["id"] for leaf in plain_leaves]
        assert getattr(first, app).missing_ids == getattr(plain, app).missing_ids
        assert shuffled_leaves != plain_leaves
        for date_field in ("airDateUtc", "inCinemas", "releaseDate"):
            if date_field in plain_leaves[0]:
                assert sorted(leaf[date_field] for leaf in shuffled_leaves) == sorted(
                    leaf[date_field] for leaf in plain_leaves
                )

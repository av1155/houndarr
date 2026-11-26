"""Pin the mock's ``/tag`` endpoint and item tags used to drive tag filters (#833)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.mock_arr.server import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _movie(client: TestClient, path: str, movie_id: int) -> dict[str, object]:
    body = client.get(f"/radarr/api/v3/{path}", params={"pageSize": 2000}).json()
    records = body["records"] if isinstance(body, dict) else body
    return next(r for r in records if r["id"] == movie_id)


def test_tag_endpoint_serves_configured_tags_and_counts_reads(client: TestClient) -> None:
    assert client.get("/radarr/api/v3/tag").json() == []

    client.put("/__tags__/radarr", json={"tags": [{"id": 9, "label": "no-upgrade"}]})

    assert client.get("/radarr/api/v3/tag").json() == [{"id": 9, "label": "no-upgrade"}]
    assert client.get("/lidarr/api/v1/tag").json() == []
    assert client.get("/__tags__/radarr").json()["requests"] == 2


def test_item_tags_reach_wanted_and_library_records(client: TestClient) -> None:
    state = client.app.state.mock_state  # type: ignore[attr-defined]
    missing_id = min(state.radarr.missing_ids)
    library_id = min(state.radarr.upgrade_ids)

    client.put("/__tags__/radarr", json={"items": {str(missing_id): [9], str(library_id): [9]}})

    assert _movie(client, "wanted/missing", missing_id)["tags"] == [9]
    assert _movie(client, "movie", library_id)["tags"] == [9]


@pytest.mark.parametrize(
    ("app", "parent"),
    [
        ("sonarr", "series"),
        ("whisparr_v2", "series"),
        ("lidarr", "artist"),
        ("readarr", "author"),
    ],
)
def test_leaf_tags_ride_on_the_parent_houndarr_reads(
    client: TestClient, app: str, parent: str
) -> None:
    store = getattr(client.app.state.mock_state, app)  # type: ignore[attr-defined]
    leaf_id = min(store.missing_ids)

    client.put(f"/__tags__/{app}", json={"items": {str(leaf_id): [4]}})

    path = f"/{app}/api/{store.api_version}/wanted/missing"
    records = client.get(path, params={"pageSize": 2000}).json()
    leaf = next(r for r in records["records"] if r["id"] == leaf_id)
    assert leaf[parent]["tags"] == [4]


@pytest.mark.parametrize(("config", "status"), [({"status": 503}, 503), ({"html": True}, 200)])
def test_failure_modes(client: TestClient, config: dict[str, object], status: int) -> None:
    client.put("/__tags__/radarr", json={"tags": [{"id": 9, "label": "x"}], **config})

    resp = client.get("/radarr/api/v3/tag")

    assert resp.status_code == status
    if config.get("html"):
        assert resp.headers["content-type"].startswith("text/html")


def test_reset_clears_tags_failure_mode_and_item_tags(client: TestClient) -> None:
    state = client.app.state.mock_state  # type: ignore[attr-defined]
    missing_id = min(state.radarr.missing_ids)
    client.put(
        "/__tags__/radarr",
        json={"tags": [{"id": 9, "label": "x"}], "items": {str(missing_id): [9]}, "status": 500},
    )

    client.post("/__reset__/radarr")

    assert client.get("/radarr/api/v3/tag").json() == []
    assert "tags" not in _movie(client, "wanted/missing", missing_id)
    assert client.get("/__tags__/radarr").json()["requests"] == 1

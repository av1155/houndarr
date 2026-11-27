"""Pin the mock's Readarr cutoff rows: one per below-cutoff file, as Readarr counts them."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.mock_arr._common import arm_wanted_fault
from tests.mock_arr.server import SeedConfig, create_app
from tests.mock_arr.store import AppData

_FILES = 3


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(SeedConfig(readarr_cutoff_files=_FILES)))


def _store(client: TestClient) -> AppData:
    return client.app.state.mock_state.readarr  # type: ignore[attr-defined,no-any-return]


def _page(client: TestClient, kind: str, page: int = 1, size: int = 2000) -> dict[str, Any]:
    params = {"page": page, "pageSize": size, "sortKey": "releaseDate"}
    body: dict[str, Any] = client.get(f"/readarr/api/v1/wanted/{kind}", params=params).json()
    return body


def _ids(body: dict[str, Any]) -> list[int]:
    return [record["id"] for record in body["records"]]


def test_each_cutoff_book_fills_one_adjacent_row_per_file(client: TestClient) -> None:
    store = _store(client)

    body = _page(client, "cutoff")

    ids = _ids(body)
    assert body["totalRecords"] == len(ids) == _FILES * len(store.cutoff_ids)
    assert Counter(ids) == dict.fromkeys(store.cutoff_ids, _FILES)
    assert all(len(set(ids[i : i + _FILES])) == 1 for i in range(0, len(ids), _FILES))


def test_the_missing_list_keeps_one_row_per_book(client: TestClient) -> None:
    body = _page(client, "missing")

    assert sorted(_ids(body)) == sorted(_store(client).missing_ids)


def test_a_book_that_leaves_takes_every_row_with_it(client: TestClient) -> None:
    store = _store(client)
    books = len(store.cutoff_ids)
    arm_wanted_fault(store, "cutoff", 2, "leave", page_size=10)

    body = _page(client, "cutoff", page=2, size=10)

    [fault] = store.fired_faults
    assert body["totalRecords"] == _FILES * (books - 1)
    assert fault["left"] not in _ids(_page(client, "cutoff"))

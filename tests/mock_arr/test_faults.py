"""Pin the mock's ``/wanted`` faults that change a list between page reads (#897)."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from tests.mock_arr._common import arm_wanted_fault
from tests.mock_arr.server import create_app
from tests.mock_arr.store import AppData, WantedFaultOp

_SIZE = 10
_PATH = "/sonarr/api/v3/wanted/missing"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _store(client: TestClient) -> AppData:
    return client.app.state.mock_state.sonarr  # type: ignore[attr-defined,no-any-return]


def _page(client: TestClient, page: int, size: int = _SIZE) -> dict[str, Any]:
    params = {
        "page": page,
        "pageSize": size,
        "sortKey": "airDateUtc",
        "sortDirection": "ascending",
    }
    body: dict[str, Any] = client.get(_PATH, params=params).json()
    return body


def _ids(body: dict[str, Any]) -> list[int]:
    return [record["id"] for record in body["records"]]


def _order(client: TestClient) -> list[int]:
    return _ids(_page(client, 1, size=2000))


def _arm(client: TestClient, op: str, page: int = 2, *, sticky: bool = False) -> None:
    arm_wanted_fault(_store(client), "missing", page, op, page_size=_SIZE, sticky=sticky)


def test_leave_drops_the_boundary_item_so_the_next_one_goes_unread(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "leave")

    body = _page(client, 2)

    assert _ids(body) == order[_SIZE + 1 : 2 * _SIZE + 1]
    assert body["totalRecords"] == len(order) - 1
    store = _store(client)
    assert order[_SIZE - 1] in store.upgrade_ids
    assert order[_SIZE - 1] not in store.missing_ids
    [event] = store.fired_faults
    assert event["left"] == order[_SIZE - 1]
    assert event["skipped"] == order[_SIZE]
    assert (event["n_before"], event["n_after"]) == (len(order), len(order) - 1)


def test_leave_unread_drops_an_item_no_one_read(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "leave_unread")

    body = _page(client, 2)

    assert _ids(body) == order[_SIZE + 1 : 2 * _SIZE + 1]
    assert body["totalRecords"] == len(order) - 1
    [event] = _store(client).fired_faults
    assert event["left"] == order[_SIZE]
    assert event["skipped"] is None


def test_join_adds_an_item_at_or_after_the_offset(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "join")

    body = _page(client, 2)

    [event] = _store(client).fired_faults
    joined = event["joined"]
    assert joined in _store(client).missing_ids
    assert body["totalRecords"] == len(order) + 1
    after = _order(client)
    assert after.index(joined) >= _SIZE
    assert after[:_SIZE] == order[:_SIZE]


def test_join_before_serves_the_boundary_item_twice(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "join_before")

    body = _page(client, 2)

    [event] = _store(client).fired_faults
    assert _ids(body)[0] == order[_SIZE - 1]
    assert event["duplicated"] == order[_SIZE - 1]
    assert _order(client).index(event["joined"]) < _SIZE
    assert body["totalRecords"] == len(order) + 1


def test_leave_join_skips_an_item_and_keeps_the_total(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "leave_join")

    body = _page(client, 2)

    [event] = _store(client).fired_faults
    assert body["totalRecords"] == len(order)
    assert event["skipped"] == order[_SIZE]
    assert event["joined"] is not None
    assert order[_SIZE] not in _ids(body)
    assert _order(client).index(order[_SIZE]) == _SIZE - 1


def test_tie_split_swaps_one_response_and_leaves_the_list_alone(client: TestClient) -> None:
    order = _order(client)
    missing_before = set(_store(client).missing_ids)
    _arm(client, "tie_split")

    split = _page(client, 2)
    clean = _page(client, 2)

    assert _ids(split) == [order[_SIZE - 1], *order[_SIZE + 1 : 2 * _SIZE]]
    assert split["totalRecords"] == len(order)
    assert _ids(clean) == order[_SIZE : 2 * _SIZE]
    assert _store(client).missing_ids == missing_before
    [event] = _store(client).fired_faults
    assert (event["duplicated"], event["skipped"]) == (order[_SIZE - 1], order[_SIZE])


def test_leave_after_records_counts_the_list_after_its_records(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "leave_after_records", page=1)

    first = _page(client, 1)
    second = _page(client, 2)

    assert _ids(first) == order[:_SIZE]
    assert first["totalRecords"] == len(order) - 1
    assert _ids(second) == order[_SIZE + 1 : 2 * _SIZE + 1]
    [event] = _store(client).fired_faults
    assert (event["left"], event["skipped"]) == (order[_SIZE - 1], order[_SIZE])


def test_a_sticky_fault_fires_on_every_matching_read(client: TestClient) -> None:
    _arm(client, "tie_split", sticky=True)

    _page(client, 2)
    _page(client, 2)

    assert len(_store(client).fired_faults) == 2
    assert ("missing", 2, _SIZE) in _store(client).wanted_faults


def test_a_fault_matches_its_page_size_only(client: TestClient) -> None:
    _arm(client, "leave")

    _page(client, 2, size=1)
    _page(client, 2, size=_SIZE + 1)

    assert _store(client).fired_faults == []


def test_a_fault_past_the_list_end_is_a_no_op(client: TestClient) -> None:
    order = _order(client)
    _arm(client, "leave", page=len(order) // _SIZE + 2)

    _page(client, len(order) // _SIZE + 2)

    assert _store(client).fired_faults == []
    assert len(_order(client)) == len(order)


@pytest.mark.parametrize(
    "op", [op for op in get_args(WantedFaultOp.__value__) if op != "leave_after_records"]
)
def test_ops_that_need_a_page_boundary_refuse_page_one(client: TestClient, op: str) -> None:
    with pytest.raises(ValueError, match="page must be 2 or more"):
        _arm(client, op, page=1)


def test_put_arms_lists_and_refuses_a_bad_fault(client: TestClient) -> None:
    good = {"kind": "missing", "page": 2, "op": "leave", "sticky": True}

    armed = client.put("/__faults__/sonarr", json={"faults": [good]})
    refused = client.put(
        "/__faults__/sonarr", json={"faults": [good, {"kind": "missing", "page": 1, "op": "join"}]}
    )

    assert armed.json()["armed"] == [{**good, "page_size": 250}]
    assert refused.status_code == 400
    assert client.get("/__faults__/sonarr").json() == {"armed": [], "fired": []}


def test_reset_restores_the_seeded_partition_and_clears_faults(client: TestClient) -> None:
    store = _store(client)
    seeded = (set(store.missing_ids), set(store.cutoff_ids), set(store.upgrade_ids))
    _arm(client, "leave")
    _arm(client, "join", page=3, sticky=True)
    _page(client, 2)
    _page(client, 3)

    client.post("/__reset__/sonarr")

    assert (store.missing_ids, store.cutoff_ids, store.upgrade_ids) == seeded
    assert client.get("/__faults__/sonarr").json() == {"armed": [], "fired": []}

"""Lidarr and Readarr keep cutoff-unmet items out of the upgrade pool however the list pages.

Their upgrade pool is every monitored album or book with a file, minus
the cutoff-unmet list, which the cutoff pass already searches.  That list
is read the way reconcile reads it, so a tie split a re-read covers stays
excluded, and reconcile builds its upgrade set from the same function.
A page that fails ends the read: the pass defers or writes its error row
instead of searching with part of the list excluded.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from houndarr.engine.adapters import _common
from houndarr.engine.adapters import lidarr as lidarr_adapter
from houndarr.engine.adapters import readarr as readarr_adapter
from houndarr.engine.adapters._common import _RECONCILE_PAGE_SIZE
from houndarr.engine.search_loop import CycleReport, run_instance_search
from houndarr.services.instances import InstanceType
from tests.mock_arr._common import arm_wanted_fault
from tests.mock_arr.server import SeedConfig, create_app

from .conftest import MASTER_KEY, get_log_rows, make_instance

_MOCK = "http://mock-arr"

_APPS = [
    pytest.param(lidarr_adapter, 3, InstanceType.lidarr, "lidarr", "album", id="lidarr"),
    pytest.param(readarr_adapter, 4, InstanceType.readarr, "readarr", "book", id="readarr"),
]

_LARGE = SeedConfig(shuffle_dates=True, lidarr_artists=200, readarr_authors=200)


def _serve_mock(config: SeedConfig | None = None) -> Any:
    mock = create_app(config)
    respx.route(host="mock-arr").mock(side_effect=respx.ASGIHandler(mock))
    return mock.state.mock_state


def _instance(instance_id: int, itype: InstanceType, app: str, **kwargs: Any) -> Any:
    return make_instance(
        instance_id=instance_id,
        itype=itype,
        url=f"{_MOCK}/{app}",
        upgrade_enabled=True,
        **kwargs,
    )


async def _pool_matching_reconcile(module: Any, instance: Any) -> set[int]:
    """Return the cycle's upgrade pool ids, after checking reconcile's upgrade set matches it."""
    async with module.make_client(instance) as client:
        pool = await module.fetch_upgrade_pool(client, instance)
        sets = await module.fetch_reconcile_sets(client, instance)
    candidates = [module.adapt_upgrade(item, instance) for item in pool]
    assert sets.upgrade == {(str(c.item_type), c.item_id) for c in candidates}
    return {c.item_id for c in candidates}


@pytest.mark.asyncio()
@pytest.mark.parametrize(("module", "instance_id", "itype", "app", "item_type"), _APPS)
@respx.mock
async def test_the_upgrade_pool_is_every_item_with_a_file_past_its_cutoff(
    module: Any, instance_id: int, itype: InstanceType, app: str, item_type: str
) -> None:
    data = getattr(_serve_mock(_LARGE), app)
    assert len(data.cutoff_ids) > _RECONCILE_PAGE_SIZE, "the cutoff list must span pages"

    pool = await _pool_matching_reconcile(module, _instance(instance_id, itype, app))

    assert pool == data.upgrade_ids
    cutoff_pages = len(data.cutoff_ids) // _RECONCILE_PAGE_SIZE + 1
    requests = Counter((k, size) for k, _page, size in data.page_log.entries if k == "cutoff")
    assert requests == {("cutoff", 1): 2, ("cutoff", _RECONCILE_PAGE_SIZE): 2 * cutoff_pages}


@pytest.mark.asyncio()
@respx.mock
async def test_a_tie_split_a_reread_covers_keeps_the_album_out_of_the_pool() -> None:
    data = _serve_mock(_LARGE).lidarr
    arm_wanted_fault(data, "cutoff", 2, "tie_split", page_size=_RECONCILE_PAGE_SIZE, sticky=True)

    instance = _instance(3, InstanceType.lidarr, "lidarr")

    pool = await _pool_matching_reconcile(lidarr_adapter, instance)

    assert data.fired_faults[0]["skipped"] in data.cutoff_ids
    assert pool == data.upgrade_ids


@pytest.mark.asyncio()
@respx.mock
async def test_a_readarr_cutoff_tie_split_leaves_its_book_in_the_pool() -> None:
    """Known residual: Readarr counts cutoff rows, so a repeated row reads as a second file."""
    data = _serve_mock(_LARGE).readarr
    arm_wanted_fault(data, "cutoff", 2, "tie_split", page_size=_RECONCILE_PAGE_SIZE, sticky=True)

    pool = await _pool_matching_reconcile(
        readarr_adapter, _instance(4, InstanceType.readarr, "readarr")
    )

    skipped = data.fired_faults[0]["skipped"]
    assert pool == data.upgrade_ids | {skipped}


@pytest.mark.asyncio()
@pytest.mark.parametrize(("module", "instance_id", "itype", "app", "item_type"), _APPS)
@respx.mock
async def test_an_item_the_read_slid_past_stays_in_that_pool(
    caplog: pytest.LogCaptureFixture,
    module: Any,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
) -> None:
    """A list that moved mid-read excludes what it read; the miss costs one search at most."""
    data = getattr(_serve_mock(_LARGE), app)
    arm_wanted_fault(data, "cutoff", 2, "leave", page_size=_RECONCILE_PAGE_SIZE)
    instance = _instance(instance_id, itype, app)

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        async with module.make_client(instance) as client:
            pool = {
                module.adapt_upgrade(item, instance).item_id
                for item in await module.fetch_upgrade_pool(client, instance)
            }

    [fault] = data.fired_faults
    assert pool == (data.upgrade_ids - {fault["left"]}) | {fault["skipped"]}
    assert [r for r in caplog.records if r.name == module.__name__] == []


@pytest.mark.asyncio()
@pytest.mark.parametrize(("module", "instance_id", "itype", "app", "item_type"), _APPS)
@respx.mock
async def test_a_cutoff_list_past_the_page_cap_warns_and_excludes_what_it_read(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    module: Any,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 1)
    data = getattr(_serve_mock(_LARGE), app)
    noun = f"{item_type}s"

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        pool = await _pool_matching_reconcile(module, _instance(instance_id, itype, app))

    assert len(data.cutoff_ids - pool) == _RECONCILE_PAGE_SIZE
    assert pool - data.cutoff_ids == data.upgrade_ids
    warnings = [r.getMessage() for r in caplog.records if r.name == module.__name__]
    assert warnings == [
        f"[Test Instance] cutoff exclusion walk hit its page cap after {_RECONCILE_PAGE_SIZE} "
        f"{noun}; the upgrade pool may include some that are still cutoff-unmet"
    ]


def _cutoff_route(app: str) -> str:
    return f"{_MOCK}/{app}/api/v1/wanted/cutoff"


@pytest.mark.asyncio()
@pytest.mark.parametrize(("module", "instance_id", "itype", "app", "item_type"), _APPS)
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_a_cutoff_page_that_fails_defers_the_upgrade_pass(
    mock_update: AsyncMock,
    seeded_instances: None,
    module: Any,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
) -> None:
    respx.get(_cutoff_route(app), params={"page": "2", "pageSize": "250"}).mock(
        return_value=httpx.Response(503)
    )
    data = getattr(_serve_mock(_LARGE), app)
    instance = _instance(instance_id, itype, app, missing_enabled=False, upgrade_batch_size=5)

    report = CycleReport()
    count = await run_instance_search(instance, MASTER_KEY, report=report)

    assert count == 0
    assert report.upgrade_deferred is True
    assert data.command_log.entries == []
    assert [r for r in await get_log_rows() if r["search_kind"] == "upgrade"] == []


@pytest.mark.asyncio()
@pytest.mark.parametrize(("module", "instance_id", "itype", "app", "item_type"), _APPS)
@respx.mock
@patch("houndarr.engine.search_loop.update_instance", new_callable=AsyncMock)
async def test_a_refused_cutoff_list_writes_the_upgrade_error_row(
    mock_update: AsyncMock,
    seeded_instances: None,
    module: Any,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
) -> None:
    respx.get(_cutoff_route(app)).mock(return_value=httpx.Response(401))
    data = getattr(_serve_mock(_LARGE), app)
    instance = _instance(instance_id, itype, app, missing_enabled=False, upgrade_batch_size=5)

    report = CycleReport()
    count = await run_instance_search(instance, MASTER_KEY, report=report)

    assert count == 0
    assert report.upgrade_deferred is False
    assert data.command_log.entries == []
    rows = [r for r in await get_log_rows() if r["search_kind"] == "upgrade"]
    assert [(r["action"], r["message"]) for r in rows] == [
        ("error", f"upgrade pool fetch failed: HTTP 401 from /{app}/api/v1/wanted/cutoff"),
    ]

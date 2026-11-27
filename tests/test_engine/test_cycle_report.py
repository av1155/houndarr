"""A cycle reports the *arr reached only when the *arr answered a request.

The supervisor closes an open error only after a cycle that reached the
*arr, so each read that sets ``reached`` is pinned here against a real
``run_instance_search`` cycle.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import pytest
import respx

from houndarr.engine.search_loop import CycleReport, run_instance_search
from houndarr.services.instances import InstanceType, TagFilterPolicy

from .conftest import MASTER_KEY, RADARR_URL, SONARR_URL, make_instance

_EMPTY_PAGE = {"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}


async def _run(*, itype: InstanceType = InstanceType.sonarr, **overrides: Any) -> CycleReport:
    tag_filter = overrides.pop("tag_filter", None)
    instance = make_instance(instance_id=1, itype=itype, **overrides)
    if tag_filter is not None:
        instance = dataclasses.replace(instance, tag_filter=tag_filter)
    report = CycleReport()
    await run_instance_search(instance, MASTER_KEY, report=report)
    return report


@pytest.mark.asyncio()
@respx.mock
async def test_an_empty_wanted_page_counts_as_reached(seeded_instances: None) -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE)
    )

    report = await _run()

    assert report == CycleReport(reached=True)


@pytest.mark.asyncio()
@respx.mock
async def test_an_empty_cutoff_page_counts_as_reached(seeded_instances: None) -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE)
    )

    report = await _run(missing_enabled=False, cutoff_enabled=True)

    assert report == CycleReport(reached=True)


@pytest.mark.asyncio()
@respx.mock
async def test_an_upgrade_list_read_counts_as_reached(seeded_instances: None) -> None:
    respx.get(f"{RADARR_URL}/api/v3/movie").mock(return_value=httpx.Response(200, json=[]))

    report = await _run(itype=InstanceType.radarr, missing_enabled=False, upgrade_enabled=True)

    assert report == CycleReport(reached=True)


@pytest.mark.asyncio()
@respx.mock
@pytest.mark.parametrize(("queued", "skipped"), [(0, False), (5, True)])
async def test_a_queue_status_read_counts_as_reached(
    seeded_instances: None, queued: int, skipped: bool
) -> None:
    respx.get(f"{SONARR_URL}/api/v3/queue/status").mock(
        return_value=httpx.Response(200, json={"totalCount": queued})
    )

    report = await _run(missing_enabled=False, queue_limit=5)

    assert report == CycleReport(reached=True, passes_skipped=skipped)


@pytest.mark.asyncio()
@respx.mock
async def test_a_tag_list_read_counts_as_reached(seeded_instances: None) -> None:
    respx.get(f"{SONARR_URL}/api/v3/tag").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "label": "keep"}])
    )

    report = await _run(batch_size=0, tag_filter=TagFilterPolicy(include=("keep",)))

    assert report == CycleReport(reached=True)


@pytest.mark.asyncio()
async def test_a_cycle_that_sends_no_request_is_not_reached(seeded_instances: None) -> None:
    report = await _run(missing_enabled=False)

    assert report == CycleReport()

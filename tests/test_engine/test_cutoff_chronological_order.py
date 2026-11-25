"""Chronological cutoff passes walk the cutoff list oldest-first.

Without ``sortKey`` and ``sortDirection`` each app serves its own order:
newest first on Sonarr 4 and Whisparr v2, most recently added first on
Lidarr and Readarr, and HTTP 500 on Sonarr 3.0.10.1567.  The mock models
those fallbacks and stores release dates out of id order, so only a
correctly sorted request dispatches oldest-first.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import InstanceType, SearchOrder, get_instance
from tests.mock_arr.server import SeedConfig, create_app

from .conftest import MASTER_KEY, make_instance

_CYCLES = 4
_BATCH = 5


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("instance_id", "itype", "app", "leaf_key", "date_field"),
    [
        (1, InstanceType.sonarr, "sonarr", "episodeIds", "airDateUtc"),
        (3, InstanceType.lidarr, "lidarr", "albumIds", "releaseDate"),
        (4, InstanceType.readarr, "readarr", "bookIds", "releaseDate"),
        (5, InstanceType.whisparr_v2, "whisparr_v2", "episodeIds", "releaseDate"),
    ],
    ids=["sonarr", "lidarr", "readarr", "whisparr_v2"],
)
@respx.mock
async def test_chronological_cutoff_pass_dispatches_oldest_first(
    seeded_instances: None,
    instance_id: int,
    itype: InstanceType,
    app: str,
    leaf_key: str,
    date_field: str,
) -> None:
    mock = create_app(SeedConfig(shuffle_dates=True))
    respx.route(host="mock-arr").mock(side_effect=respx.ASGIHandler(mock))
    data = getattr(mock.state.mock_state, app)
    dates = {leaf["id"]: leaf[date_field] for leaf in data.leaves}
    instance = make_instance(
        instance_id=instance_id,
        itype=itype,
        url=f"http://mock-arr/{app}",
        missing_enabled=False,
        cutoff_enabled=True,
        cutoff_batch_size=_BATCH,
        cutoff_hourly_cap=0,
        post_release_grace_hrs=0,
        search_order=SearchOrder.chronological,
    )

    for _ in range(_CYCLES):
        await run_instance_search(instance, MASTER_KEY)
        stored = await get_instance(instance_id, master_key=MASTER_KEY)
        assert stored is not None
        offset = stored.schedule.cutoff_page_offset
        instance = replace(instance, schedule=replace(instance.schedule, cutoff_page_offset=offset))

    dispatched = [item for command in data.command_log.entries for item in command[leaf_key]]
    oldest = sorted(dates[item] for item in data.cutoff_ids)
    assert [dates[item] for item in dispatched] == oldest[: _CYCLES * _BATCH]

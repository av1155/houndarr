"""One snapshot refresh reads each ``/wanted`` list once, and never reaps from part of one.

The dashboard snapshot and the cooldown reconcile both need the full
missing list, and Lidarr and Readarr also exclude the cutoff list from
the upgrade pool.  Each step used to walk its own copy, so a refresh paid
for every list twice.  A list that could not be read in full (past the
page cap, or a series that failed to load) used to feed reconcile anyway,
which then deleted the cooldowns of every item it never saw.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import httpx
import pytest
import respx

from houndarr.clients.base import WantedWalks
from houndarr.database import get_db
from houndarr.engine.adapters import _common
from houndarr.engine.adapters import lidarr as lidarr_adapter
from houndarr.engine.adapters import readarr as readarr_adapter
from houndarr.engine.adapters._common import paginate_wanted, walk_wanted
from houndarr.engine.supervisor import Supervisor
from houndarr.services.instances import InstanceType, get_instance
from tests.mock_arr.server import SeedConfig, create_app

from .conftest import MASTER_KEY, make_instance

_MOCK = "http://mock-arr"

_PAGED_APPS = [
    (1, InstanceType.sonarr, "sonarr", "episode"),
    (2, InstanceType.radarr, "radarr", "movie"),
    (3, InstanceType.lidarr, "lidarr", "album"),
    (4, InstanceType.readarr, "readarr", "book"),
    (5, InstanceType.whisparr_v2, "whisparr_v2", "whisparr_v2_episode"),
]


def _serve_mock(config: SeedConfig | None = None) -> Any:
    mock = create_app(config)
    respx.route(host="mock-arr").mock(side_effect=respx.ASGIHandler(mock))
    return mock.state.mock_state


async def _seed_cooldowns(instance_id: int, item_type: str, rows: list[tuple[int, str]]) -> None:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)"
            " VALUES (?, ?, ?, ?, datetime('now'))",
            [(instance_id, item_id, item_type, kind) for item_id, kind in rows],
        )
        await conn.commit()


async def _cooldown_ids(instance_id: int) -> set[int]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_id FROM cooldowns WHERE instance_id = ?", (instance_id,)
        ) as cur:
            return {int(row["item_id"]) for row in await cur.fetchall()}


def _full_walk_pages(count: int) -> int:
    return count // 250 + 1


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("instance_id", "itype", "app", "item_type"),
    _PAGED_APPS,
    ids=[app for _, _, app, _ in _PAGED_APPS],
)
@respx.mock
async def test_one_refresh_walks_each_wanted_list_once(
    seeded_instances: None, instance_id: int, itype: InstanceType, app: str, item_type: str
) -> None:
    data = getattr(_serve_mock(), app)
    wanted, gone = min(data.missing_ids), min(data.upgrade_ids)
    await _seed_cooldowns(instance_id, item_type, [(wanted, "missing"), (gone, "missing")])
    instance = make_instance(
        instance_id=instance_id,
        itype=itype,
        url=f"{_MOCK}/{app}",
        cutoff_enabled=True,
        upgrade_enabled=True,
    )

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    pages = Counter((kind, size) for kind, _page, size in data.page_log.entries)
    assert pages[("missing", 250)] == _full_walk_pages(len(data.missing_ids))
    assert pages[("cutoff", 250)] == _full_walk_pages(len(data.cutoff_ids))
    assert await _cooldown_ids(instance_id) == {wanted}
    stored = await get_instance(instance_id, master_key=MASTER_KEY)
    assert stored is not None
    assert stored.snapshot.monitored_total == len(data.missing_ids) + len(data.cutoff_ids)


@pytest.mark.asyncio()
@respx.mock
async def test_whisparr_v3_refresh_reads_its_library_once(seeded_instances: None) -> None:
    mock = create_app()
    library = respx.get(f"{_MOCK}/whisparr_v3/api/v3/movie").mock(
        side_effect=respx.ASGIHandler(mock)
    )
    respx.route(host="mock-arr").mock(side_effect=respx.ASGIHandler(mock))
    data = mock.state.mock_state.whisparr_v3
    wanted = min(data.missing_ids)
    await _seed_cooldowns(6, "whisparr_v3_movie", [(wanted, "missing"), (999_999, "missing")])
    instance = make_instance(
        instance_id=6,
        itype=InstanceType.whisparr_v3,
        url=f"{_MOCK}/whisparr_v3",
        cutoff_enabled=True,
        upgrade_enabled=True,
    )

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    assert library.call_count == 1
    assert await _cooldown_ids(6) == {wanted}


_LARGE = SeedConfig(
    shuffle_dates=True,
    sonarr_series=200,
    radarr_movies=2000,
    lidarr_artists=200,
    readarr_authors=200,
    whisparr_v2_series=200,
)


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("instance_id", "itype", "app", "item_type"),
    _PAGED_APPS,
    ids=[app for _, _, app, _ in _PAGED_APPS],
)
@respx.mock
async def test_lists_past_the_page_cap_keep_their_cooldowns(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 1)
    data = getattr(_serve_mock(_LARGE), app)
    assert min(len(data.missing_ids), len(data.cutoff_ids)) > 250, "both lists must overflow"
    unseen_missing, unseen_cutoff = sorted(data.upgrade_ids)[:2]
    await _seed_cooldowns(
        instance_id, item_type, [(unseen_missing, "missing"), (unseen_cutoff, "cutoff")]
    )
    instance = make_instance(
        instance_id=instance_id, itype=itype, url=f"{_MOCK}/{app}", cutoff_enabled=True
    )
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await supervisor._refresh_one_snapshot(instance)
        await supervisor._refresh_one_snapshot(instance)

    assert await _cooldown_ids(instance_id) == {unseen_missing, unseen_cutoff}
    messages = [
        (r.levelno, r.getMessage()) for r in caplog.records if "incomplete" in r.getMessage()
    ]
    assert messages == [
        (
            logging.WARNING,
            "Supervisor: 'Test Instance' missing and cutoff lists incomplete; keeping those "
            "cooldowns until a refresh reads them in full",
        ),
        (logging.DEBUG, "Supervisor: 'Test Instance' missing and cutoff lists still incomplete"),
    ]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("module", "itype", "app"),
    [
        (lidarr_adapter, InstanceType.lidarr, "lidarr"),
        (readarr_adapter, InstanceType.readarr, "readarr"),
    ],
    ids=["lidarr", "readarr"],
)
@pytest.mark.parametrize("exclusion_pages", [1, None], ids=["cycle-cap-hit", "default-cap"])
@respx.mock
async def test_reconcile_upgrade_set_matches_the_cycle_pool(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    itype: InstanceType,
    app: str,
    exclusion_pages: int | None,
) -> None:
    if exclusion_pages is not None:
        monkeypatch.setattr(module, "_UPGRADE_CUTOFF_EXCLUSION_HARD_CAP", exclusion_pages)
    data = getattr(_serve_mock(_LARGE), app)
    assert len(data.cutoff_ids) > 250, "the cutoff list must outrun a one-page exclusion"
    instance = make_instance(itype=itype, url=f"{_MOCK}/{app}", upgrade_enabled=True)

    async with module.make_client(instance) as client:
        pool = await module.fetch_upgrade_pool(client, instance)
        sets = await module.fetch_reconcile_sets(client, instance)

    pool_pairs = {
        (str(c.item_type), c.item_id) for c in (module.adapt_upgrade(i, instance) for i in pool)
    }
    assert sets.upgrade == pool_pairs
    excluded = len(data.leaves) - len(data.missing_ids) - len(pool)
    assert excluded == (250 if exclusion_pages == 1 else len(data.cutoff_ids))


@pytest.mark.asyncio()
@respx.mock
async def test_a_failed_reconcile_fetch_does_not_end_the_incomplete_streak(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 1)
    mock = create_app()
    handler = respx.ASGIHandler(mock)
    cutoff_walk = respx.get(url__regex=r"/sonarr/api/v3/wanted/cutoff\?page=1&pageSize=250&")
    cutoff_walk.mock(side_effect=handler)
    respx.route(host="mock-arr").mock(side_effect=handler)
    instance = make_instance(
        instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr", cutoff_enabled=True
    )
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await supervisor._refresh_one_snapshot(instance)
        cutoff_walk.mock(return_value=httpx.Response(500))
        await supervisor._refresh_one_snapshot(instance)
        cutoff_walk.mock(side_effect=handler)
        await supervisor._refresh_one_snapshot(instance)

    levels = [
        r.levelno
        for r in caplog.records
        if "list" in r.getMessage() and "incomplete" in r.getMessage()
    ]
    assert levels == [logging.WARNING, logging.DEBUG]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("instance_id", "itype", "app", "item_type"),
    [
        (1, InstanceType.sonarr, "sonarr", "episode"),
        (5, InstanceType.whisparr_v2, "whisparr_v2", "whisparr_v2_episode"),
    ],
    ids=["sonarr", "whisparr_v2"],
)
@respx.mock
async def test_a_series_that_fails_to_load_keeps_upgrade_cooldowns(
    seeded_instances: None, instance_id: int, itype: InstanceType, app: str, item_type: str
) -> None:
    respx.get(url__regex=rf"/{app}/api/v3/episode\?seriesId=1&").respond(500)
    data = getattr(_serve_mock(), app)
    unseen = min(data.missing_ids)
    await _seed_cooldowns(instance_id, item_type, [(unseen, "upgrade")])
    instance = make_instance(
        instance_id=instance_id, itype=itype, url=f"{_MOCK}/{app}", upgrade_enabled=True
    )

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    assert await _cooldown_ids(instance_id) == {unseen}


@pytest.mark.asyncio()
@respx.mock
async def test_a_dropped_missing_page_leaves_snapshot_and_cooldowns_alone(
    seeded_instances: None,
) -> None:
    respx.get(url__regex=r"/sonarr/api/v3/wanted/missing\?page=2&").mock(
        side_effect=httpx.ConnectError("refused")
    )
    data = _serve_mock().sonarr
    await _seed_cooldowns(1, "episode", [(min(data.upgrade_ids), "missing")])
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    assert await _cooldown_ids(1) == {min(data.upgrade_ids)}
    stored = await get_instance(1, master_key=MASTER_KEY)
    assert stored is not None
    assert stored.snapshot.monitored_total == 0


@pytest.mark.asyncio()
@respx.mock
async def test_a_failed_cutoff_walk_still_writes_the_snapshot(seeded_instances: None) -> None:
    respx.get(url__regex=r"/sonarr/api/v3/wanted/cutoff\?page=1&pageSize=250&").respond(500)
    data = _serve_mock().sonarr
    await _seed_cooldowns(1, "episode", [(min(data.upgrade_ids), "missing")])
    instance = make_instance(
        instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr", cutoff_enabled=True
    )

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    assert await _cooldown_ids(1) == {min(data.upgrade_ids)}
    stored = await get_instance(1, master_key=MASTER_KEY)
    assert stored is not None
    assert stored.snapshot.monitored_total == len(data.missing_ids) + len(data.cutoff_ids)


class _Pages:
    def __init__(self, total: int, *, fail_on: int | None = None) -> None:
        self.total = total
        self.fail_on = fail_on
        self.calls: list[int] = []

    async def __call__(self, *, page: int, page_size: int) -> list[int]:
        self.calls.append(page)
        if page == self.fail_on:
            raise httpx.ConnectError("refused")
        start = (page - 1) * page_size
        return list(range(start, min(start + page_size, self.total)))


@pytest.mark.asyncio()
async def test_walk_reaches_the_short_page_and_reports_complete() -> None:
    walk = await paginate_wanted(_Pages(600))
    assert (len(walk.items), walk.complete) == (600, True)


@pytest.mark.asyncio()
async def test_walk_stopped_by_the_page_cap_reports_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 2)
    walk = await paginate_wanted(_Pages(600))
    assert (len(walk.items), walk.complete) == (500, False)


@pytest.mark.asyncio()
async def test_a_memo_serves_the_second_walk_without_fetching() -> None:
    fetch = _Pages(300)
    walks: WantedWalks = {}
    first = await walk_wanted(walks, "missing", fetch)
    second = await walk_wanted(walks, "missing", fetch)
    assert second is first
    assert fetch.calls == [1, 2]


@pytest.mark.asyncio()
async def test_a_failed_walk_is_not_remembered() -> None:
    walks: WantedWalks = {}
    with pytest.raises(httpx.ConnectError):
        await walk_wanted(walks, "cutoff", _Pages(300, fail_on=2))
    assert walks == {}
    walk = await walk_wanted(walks, "cutoff", _Pages(300))
    assert walk.complete
    assert walks == {"cutoff": walk}


@pytest.mark.asyncio()
async def test_no_memo_walks_every_time() -> None:
    fetch = _Pages(10)
    await walk_wanted(None, "missing", fetch)
    await walk_wanted(None, "missing", fetch)
    assert fetch.calls == [1, 1]

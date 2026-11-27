"""One snapshot refresh reads each ``/wanted`` list once, and never reaps from part of one.

The dashboard snapshot and the cooldown reconcile both need the full
missing list, and Lidarr and Readarr also exclude the cutoff list from
the upgrade pool.  Each step used to walk its own copy, so a refresh paid
for every list twice.  A list that could not be read in full (past the
page cap, or a series that failed to load) used to feed reconcile anyway,
which then deleted the cooldowns of every item it never saw.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from houndarr.clients.base import ReconcileSets, WantedPage, WantedWalk, WantedWalks
from houndarr.database import get_db
from houndarr.engine.adapters import _common, get_adapter
from houndarr.engine.adapters import lidarr as lidarr_adapter
from houndarr.engine.adapters import radarr as radarr_adapter
from houndarr.engine.adapters import readarr as readarr_adapter
from houndarr.engine.adapters import sonarr as sonarr_adapter
from houndarr.engine.adapters import whisparr_v2 as whisparr_v2_adapter
from houndarr.engine.adapters._common import (
    _RECONCILE_PAGE_SIZE,
    read_wanted,
    walk_wanted,
)
from houndarr.engine.supervisor import Supervisor
from houndarr.errors import ClientError
from houndarr.services.instances import InstanceType, get_instance
from tests.mock_arr._common import arm_wanted_fault
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
    wanted = min(data.missing_ids)
    gone_missing, gone_cutoff = sorted(data.upgrade_ids)[:2]
    await _seed_cooldowns(
        instance_id,
        item_type,
        [(wanted, "missing"), (gone_missing, "missing"), (gone_cutoff, "cutoff")],
    )
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
@respx.mock
async def test_a_new_incomplete_streak_warns_again(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _serve_mock()
    instance = make_instance(
        instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr", cutoff_enabled=True
    )
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        for max_pages in (1, 200, 1):
            monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", max_pages)
            await supervisor._refresh_one_snapshot(instance)

    levels = [r.levelno for r in caplog.records if "incomplete" in r.getMessage()]
    assert levels == [logging.WARNING, logging.WARNING]


def test_the_incomplete_warning_lists_every_pass(caplog: pytest.LogCaptureFixture) -> None:
    instance = make_instance(itype=InstanceType.sonarr)

    with caplog.at_level(logging.WARNING, logger="houndarr.engine.supervisor"):
        Supervisor(master_key=MASTER_KEY)._report_incomplete_lists(
            instance, ReconcileSets(missing=None, cutoff=None, upgrade=None)
        )

    assert caplog.messages == [
        "Supervisor: 'Test Instance' missing, cutoff and upgrade lists incomplete; keeping "
        "those cooldowns until a refresh reads them in full"
    ]


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


_CHURN = "list shifted between page reads"
_NOT_WANTED = {"missing": 9_999_001, "cutoff": 9_999_002}


async def _seed_every_wanted(instance_id: int, item_type: str, data: Any) -> None:
    """Give every wanted item a cooldown, plus one per list for an item no list holds."""
    await _seed_cooldowns(
        instance_id,
        item_type,
        [
            *((i, "missing") for i in data.missing_ids),
            *((i, "cutoff") for i in data.cutoff_ids),
            *((item_id, kind) for kind, item_id in _NOT_WANTED.items()),
        ],
    )


async def _cooldown_rows(instance_id: int) -> set[tuple[int, str]]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_id, search_kind FROM cooldowns WHERE instance_id = ?", (instance_id,)
        ) as cur:
            return {(int(row["item_id"]), str(row["search_kind"])) for row in await cur.fetchall()}


def _reconciled(rows: set[tuple[int, str]], kind: str) -> bool:
    return (_NOT_WANTED[kind], kind) not in rows


def _incomplete(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if "incomplete" in r.getMessage()]


def _churn_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if _CHURN in r.getMessage()]


def _churn_line(kind: str, streak: int) -> str:
    noun = "refresh" if streak == 1 else "refreshes"
    return (
        f"Supervisor: 'Test Instance' {kind} {_CHURN}; keeping its cooldowns "
        f"({streak} {noun} in a row)"
    )


def _warning(*lists: str) -> tuple[int, str]:
    joined = " and ".join(lists)
    noun = "list" if len(lists) == 1 else "lists"
    return (
        logging.WARNING,
        f"Supervisor: 'Test Instance' {joined} {noun} incomplete; keeping those cooldowns "
        "until a refresh reads them in full",
    )


def _still(*lists: str) -> tuple[int, str]:
    noun = "list" if len(lists) == 1 else "lists"
    return (
        logging.DEBUG,
        f"Supervisor: 'Test Instance' {' and '.join(lists)} {noun} still incomplete",
    )


@pytest.mark.asyncio()
@respx.mock
async def test_an_import_between_pages_keeps_the_cooldown_the_walk_slid_past(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    data = _serve_mock(SeedConfig(sonarr_series=100)).sonarr
    assert len(data.missing_ids) == 2 * _RECONCILE_PAGE_SIZE
    await _seed_every_wanted(1, "episode", data)
    arm_wanted_fault(data, "missing", 2, "leave", page_size=_RECONCILE_PAGE_SIZE)
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    [fault] = data.fired_faults
    rows = await _cooldown_rows(1)
    assert fault["skipped"] in data.missing_ids
    assert (fault["skipped"], "missing") in rows
    assert not _reconciled(rows, "missing")
    assert _reconciled(rows, "cutoff")
    assert _incomplete(caplog) == []
    assert _churn_lines(caplog) == [_churn_line("missing", 1)]


_KIND_CASES = [
    pytest.param(*app, kind, id=f"{app[2]}-{kind}")
    for app in _PAGED_APPS
    for kind in ("missing", "cutoff")
]
_TIE_CASES = [case for case in _KIND_CASES if case.id != "readarr-cutoff"]


@pytest.mark.asyncio()
@pytest.mark.parametrize(("instance_id", "itype", "app", "item_type", "kind"), _KIND_CASES)
@pytest.mark.parametrize("op", ["leave", "join_before", "leave_after_records"])
@respx.mock
async def test_a_list_that_moves_mid_walk_keeps_its_cooldowns(
    seeded_instances: None,
    caplog: pytest.LogCaptureFixture,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
    kind: str,
    op: str,
) -> None:
    data = getattr(_serve_mock(_LARGE), app)
    await _seed_every_wanted(instance_id, item_type, data)
    seeded = set(data.missing_ids if kind == "missing" else data.cutoff_ids)
    page = 1 if op == "leave_after_records" else 2
    arm_wanted_fault(data, kind, page, op, page_size=_RECONCILE_PAGE_SIZE)
    instance = make_instance(instance_id=instance_id, itype=itype, url=f"{_MOCK}/{app}")

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    [fault] = data.fired_faults
    rows = await _cooldown_rows(instance_id)
    assert all((i, kind) in rows for i in seeded)
    assert not _reconciled(rows, kind)
    assert _incomplete(caplog) == []
    assert _churn_lines(caplog) == [_churn_line(kind, 1)]
    if op != "join_before":
        assert fault["skipped"] in (data.missing_ids if kind == "missing" else data.cutoff_ids)


@pytest.mark.asyncio()
@pytest.mark.parametrize(("instance_id", "itype", "app", "item_type", "kind"), _TIE_CASES)
@respx.mock
async def test_a_tie_split_across_pages_still_reconciles_and_keeps_live_cooldowns(
    seeded_instances: None,
    caplog: pytest.LogCaptureFixture,
    instance_id: int,
    itype: InstanceType,
    app: str,
    item_type: str,
    kind: str,
) -> None:
    data = getattr(_serve_mock(_LARGE), app)
    await _seed_every_wanted(instance_id, item_type, data)
    arm_wanted_fault(data, kind, 2, "tie_split", page_size=_RECONCILE_PAGE_SIZE, sticky=True)
    instance = make_instance(instance_id=instance_id, itype=itype, url=f"{_MOCK}/{app}")

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    [fault] = data.fired_faults
    rows = await _cooldown_rows(instance_id)
    wanted = data.missing_ids if kind == "missing" else data.cutoff_ids
    assert (fault["skipped"], kind) in rows
    assert all((i, kind) in rows for i in wanted)
    assert _reconciled(rows, kind)
    assert _incomplete(caplog) == []
    assert _churn_lines(caplog) == []


@pytest.mark.asyncio()
@respx.mock
async def test_a_readarr_cutoff_tie_split_still_loses_the_cooldown(
    seeded_instances: None,
) -> None:
    """Known residual: Readarr counts cutoff rows, so a repeated row reads as a second file."""
    data = _serve_mock(_LARGE).readarr
    await _seed_every_wanted(4, "book", data)
    arm_wanted_fault(data, "cutoff", 2, "tie_split", page_size=_RECONCILE_PAGE_SIZE)
    instance = make_instance(instance_id=4, itype=InstanceType.readarr, url=f"{_MOCK}/readarr")

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    [fault] = data.fired_faults
    rows = await _cooldown_rows(4)
    assert (fault["skipped"], "cutoff") not in rows
    assert _reconciled(rows, "cutoff")


@pytest.mark.asyncio()
@respx.mock
async def test_a_recovered_cutoff_album_keeps_the_upgrade_cooldown_its_cycle_stamps(
    seeded_instances: None,
) -> None:
    """The cycle's exclusion read skips the album too, so its upgrade pool still holds it."""
    data = _serve_mock(_LARGE).lidarr
    assert len(data.cutoff_ids) > _RECONCILE_PAGE_SIZE
    arm_wanted_fault(data, "cutoff", 2, "tie_split", page_size=_RECONCILE_PAGE_SIZE, sticky=True)
    instance = make_instance(
        instance_id=3, itype=InstanceType.lidarr, url=f"{_MOCK}/lidarr", upgrade_enabled=True
    )
    async with get_adapter(InstanceType.lidarr).make_client(instance) as client:
        pool = {
            album.album_id for album in await lidarr_adapter.fetch_upgrade_pool(client, instance)
        }
    skipped = data.fired_faults[0]["skipped"]
    read = next(i for i in data.cutoff_ids if i != skipped)
    assert skipped in pool
    assert read not in pool
    await _seed_cooldowns(3, "album", [(skipped, "upgrade"), (read, "upgrade")])

    await Supervisor(master_key=MASTER_KEY)._refresh_one_snapshot(instance)

    rows = await _cooldown_rows(3)
    assert (skipped, "upgrade") in rows
    assert (read, "upgrade") not in rows


@pytest.mark.asyncio()
@respx.mock
async def test_the_next_steady_refresh_reaps_what_left_during_a_moving_one(
    seeded_instances: None,
) -> None:
    data = _serve_mock(_LARGE).sonarr
    await _seed_every_wanted(1, "episode", data)
    arm_wanted_fault(data, "missing", 2, "leave", page_size=_RECONCILE_PAGE_SIZE)
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)

    await supervisor._refresh_one_snapshot(instance)
    [fault] = data.fired_faults
    assert (fault["left"], "missing") in await _cooldown_rows(1)
    await supervisor._refresh_one_snapshot(instance)

    rows = await _cooldown_rows(1)
    assert (fault["left"], "missing") not in rows
    assert (fault["skipped"], "missing") in rows
    assert _reconciled(rows, "missing")


async def _moving_refreshes(supervisor: Supervisor, instance: Any, data: Any, count: int) -> None:
    for _ in range(count):
        arm_wanted_fault(data, "missing", 2, "leave", page_size=_RECONCILE_PAGE_SIZE)
        await supervisor._refresh_one_snapshot(instance)


@pytest.mark.asyncio()
@respx.mock
async def test_six_moving_refreshes_in_a_row_warn_once(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    data = _serve_mock(_LARGE).sonarr
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await _moving_refreshes(supervisor, instance, data, 5)
        assert _incomplete(caplog) == []
        await _moving_refreshes(supervisor, instance, data, 2)

    assert _incomplete(caplog) == [_warning("missing"), _still("missing")]
    assert _churn_lines(caplog) == [_churn_line("missing", n) for n in range(1, 8)]


@pytest.mark.asyncio()
@respx.mock
async def test_a_steady_refresh_ends_the_moving_streak(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    data = _serve_mock(_LARGE).sonarr
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await _moving_refreshes(supervisor, instance, data, 5)
        await supervisor._refresh_one_snapshot(instance)
        await _moving_refreshes(supervisor, instance, data, 5)

    assert _incomplete(caplog) == []
    assert _churn_lines(caplog) == [_churn_line("missing", n) for n in (*range(1, 6), *range(1, 6))]


@pytest.mark.asyncio()
@respx.mock
async def test_a_failed_reconcile_fetch_does_not_end_the_moving_streak(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    mock = create_app(_LARGE)
    handler = respx.ASGIHandler(mock)
    cutoff_walk = respx.get(url__regex=r"/sonarr/api/v3/wanted/cutoff\?page=1&pageSize=250&")
    cutoff_walk.mock(side_effect=handler)
    respx.route(host="mock-arr").mock(side_effect=handler)
    data = mock.state.mock_state.sonarr
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await _moving_refreshes(supervisor, instance, data, 3)
        cutoff_walk.mock(return_value=httpx.Response(500))
        await _moving_refreshes(supervisor, instance, data, 1)
        cutoff_walk.mock(side_effect=handler)
        await _moving_refreshes(supervisor, instance, data, 3)

    assert _incomplete(caplog) == [_warning("missing")]
    assert _churn_lines(caplog) == [_churn_line("missing", n) for n in range(1, 7)]


@pytest.mark.asyncio()
@respx.mock
async def test_each_list_warns_on_its_own_streak(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(url__regex=r"/sonarr/api/v3/episode\?seriesId=1&").respond(500)
    data = _serve_mock(SeedConfig(sonarr_series=60)).sonarr
    instance = make_instance(
        instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr", upgrade_enabled=True
    )
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await _moving_refreshes(supervisor, instance, data, 6)

    assert _incomplete(caplog) == [
        _warning("upgrade"),
        *[_still("upgrade")] * 4,
        _warning("missing"),
        _still("upgrade"),
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_disabling_an_instance_ends_its_moving_streak(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    data = _serve_mock(_LARGE).sonarr
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await _moving_refreshes(supervisor, instance, data, 5)
        await supervisor.stop_instance_task(1)
        await _moving_refreshes(supervisor, instance, data, 5)

    assert _incomplete(caplog) == []
    assert _churn_lines(caplog) == [_churn_line("missing", n) for n in (*range(1, 6), *range(1, 6))]


@pytest.mark.asyncio()
@respx.mock
async def test_disabling_an_instance_lets_its_next_incomplete_list_warn_again(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 1)
    _serve_mock()
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await supervisor._refresh_one_snapshot(instance)
        await supervisor._refresh_one_snapshot(instance)
        await supervisor.stop_instance_task(1)
        await supervisor._refresh_one_snapshot(instance)

    assert _incomplete(caplog) == [_warning("missing"), _still("missing"), _warning("missing")]


@pytest.mark.asyncio()
@respx.mock
async def test_reenabling_an_instance_forgets_what_a_refresh_running_at_disable_kept(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 1)
    _serve_mock()
    instance = make_instance(instance_id=1, itype=InstanceType.sonarr, url=f"{_MOCK}/sonarr")
    supervisor = Supervisor(master_key=MASTER_KEY)
    monkeypatch.setattr(supervisor, "_instance_loop", AsyncMock())

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.supervisor"):
        await supervisor._refresh_one_snapshot(instance)
        await supervisor.stop_instance_task(1)
        await supervisor._refresh_one_snapshot(instance)
        assert await supervisor.start_instance_task(1, instance=instance)
        await asyncio.gather(*supervisor._prime_tasks)
        await supervisor.stop_instance_task(1)

    assert _incomplete(caplog) == [_warning("missing")] * 3


class _Pages:
    """A ``/wanted`` list read through ``get_wanted_page``; ids are its rows.

    ``before[(page, size)]`` changes the list before that request is
    served and ``after_records`` changes it between the request's records
    and its total, as an import landing between the app's two queries.
    ``views[(page, size)]`` reorders the rows for that request alone.
    """

    def __init__(
        self,
        total: int,
        *,
        fail_on: int | None = None,
        fail_size: int | None = None,
        fail_with: Exception | None = None,
        counts_rows: bool = False,
        totals: bool = True,
    ) -> None:
        self.rows = list(range(total))
        self.fail_on = fail_on
        self.fail_size = fail_size
        self.fail_with = fail_with or httpx.ConnectError("refused")
        self.counts_rows = counts_rows
        self.totals = totals
        self.total_override: int | None = None
        self.before: dict[tuple[int, int], Any] = {}
        self.after_records: dict[tuple[int, int], Any] = {}
        self.views: dict[tuple[int, int], Any] = {}
        self.calls: list[tuple[int, int]] = []

    async def __call__(
        self, kind: str = "missing", *, page: int, page_size: int
    ) -> WantedPage[int]:
        self.calls.append((page, page_size))
        if page == self.fail_on and page_size > 1 and self.fail_size in (None, page_size):
            raise self.fail_with
        if hook := self.before.get((page, page_size)):
            hook(self.rows)
        view = self.views.get((page, page_size), lambda rows: rows)(list(self.rows))
        start = (page - 1) * page_size
        chunk = view[start : start + page_size]
        if hook := self.after_records.get((page, page_size)):
            hook(self.rows)
        total = len(self.rows) if self.total_override is None else self.total_override
        return WantedPage(
            items=list(chunk),
            ids=tuple(chunk),
            total_records=total if self.totals else None,
            total_counts_rows=self.counts_rows,
        )

    def walk_sizes(self) -> list[int]:
        return sorted({size for _, size in self.calls if size > 1})


def _swap(i: int, j: int) -> Any:
    def view(rows: list[int]) -> list[int]:
        rows[i], rows[j] = rows[j], rows[i]
        return rows

    return view


def _serve_twice(serve: int, instead_of: int) -> Any:
    def view(rows: list[int]) -> list[int]:
        return [serve if row == instead_of else row for row in rows]

    return view


@pytest.mark.asyncio()
async def test_walk_reaches_the_short_page_and_reports_complete() -> None:
    fetch = _Pages(600)
    walk = await read_wanted(fetch)
    assert (walk.items, walk.complete, walk.churned) == (list(range(600)), True, False)
    assert fetch.calls == [(1, 1), (1, 250), (2, 250), (3, 250)]


@pytest.mark.asyncio()
@pytest.mark.parametrize("size", [0, 250, 500])
async def test_a_list_ending_on_a_page_boundary_is_complete(size: int) -> None:
    walk = await read_wanted(_Pages(size))
    assert (len(walk.items), walk.complete) == (size, True)


@pytest.mark.asyncio()
async def test_walk_stopped_by_the_page_cap_reports_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 2)
    walk = await read_wanted(_Pages(600))
    assert (len(walk.items), walk.complete, walk.churned) == (500, False, False)


@pytest.mark.asyncio()
@pytest.mark.parametrize("size", [500, 600], ids=["fills_the_cap", "past_the_cap"])
async def test_the_page_cap_wins_over_a_reordered_read(
    monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 2)
    fetch = _Pages(size)
    fetch.views[(2, 250)] = _swap(249, 250)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, False)
    assert fetch.walk_sizes() == [250]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("hook", "gone"),
    [
        (lambda rows: rows.pop(249), "leave"),
        (lambda rows: rows.pop(250), "leave_unread"),
        (lambda rows: rows.append(9_999), "join"),
        (lambda rows: rows.insert(0, 9_999), "join_before"),
    ],
    ids=["leave", "leave_unread", "join", "join_before"],
)
async def test_a_list_that_moves_between_page_reads_is_churned(hook: Any, gone: str) -> None:
    fetch = _Pages(600)
    fetch.before[(2, 250)] = hook
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, True)
    assert fetch.walk_sizes() == [250]


@pytest.mark.asyncio()
async def test_a_join_before_the_offset_is_churned_though_every_item_was_read() -> None:
    fetch = _Pages(600)
    fetch.before[(2, 250)] = lambda rows: rows.insert(0, 9_999)
    walk = await read_wanted(fetch)
    assert set(walk.items) == set(range(600))
    assert walk.churned


@pytest.mark.asyncio()
async def test_an_import_between_page_ones_records_and_count_is_churned() -> None:
    fetch = _Pages(600)
    fetch.after_records[(1, 250)] = lambda rows: rows.pop(249)
    walk = await read_wanted(fetch)
    assert 250 not in walk.items
    assert (walk.complete, walk.churned) == (False, True)


@pytest.mark.asyncio()
async def test_a_tie_split_is_read_again_and_covered() -> None:
    fetch = _Pages(600)
    fetch.views[(2, 250)] = _swap(249, 250)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (True, False)
    assert walk.items == [*range(250), *range(251, 600), 250]
    assert walk.first_read == [*range(250), 249, *range(251, 600)]
    assert fetch.walk_sizes() == [239, 250]


@pytest.mark.asyncio()
async def test_a_tie_split_both_reads_share_is_covered_by_the_third() -> None:
    fetch = _Pages(600)
    fetch.views[(2, 250)] = _swap(249, 250)
    fetch.views[(2, 239)] = _serve_twice(serve=238, instead_of=250)
    walk = await read_wanted(fetch)
    assert (walk.complete, sorted(walk.items)) == (True, list(range(600)))
    assert walk.first_read.count(249) == 2
    assert fetch.walk_sizes() == [239, 250, 261]


@pytest.mark.asyncio()
async def test_a_tie_split_no_read_covers_stays_churned() -> None:
    fetch = _Pages(600)
    for size in (250, 239, 261):
        fetch.views[(250 // size + 1, size)] = _serve_twice(serve=0, instead_of=250)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, True)
    assert walk.items.count(0) == 2


@pytest.mark.asyncio()
async def test_a_reread_whose_total_moved_does_not_cover_the_list() -> None:
    fetch = _Pages(600)
    fetch.views[(2, 250)] = _swap(249, 250)
    fetch.before[(1, 239)] = lambda rows: rows.append(9_999)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, True)
    assert fetch.walk_sizes() == [239, 250]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.InvalidURL("bad url"),
        ValueError("not json"),
        ClientError("bad response"),
    ],
    ids=["http", "url", "body", "client"],
)
async def test_a_reread_that_fails_leaves_the_list_churned(error: Exception) -> None:
    fetch = _Pages(600, fail_on=2, fail_size=239, fail_with=error)
    fetch.views[(2, 250)] = _swap(249, 250)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, True)
    assert walk.items.count(249) == 2
    assert fetch.walk_sizes() == [239, 250]


@pytest.mark.asyncio()
async def test_a_reread_that_would_stop_at_the_page_cap_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_common, "_RECONCILE_MAX_PAGES", 3)
    fetch = _Pages(720)
    fetch.views[(2, 250)] = _swap(249, 250)
    walk = await read_wanted(fetch)
    assert (walk.complete, sorted(walk.items)) == (True, list(range(720)))
    assert fetch.walk_sizes() == [250, 261]


@pytest.mark.asyncio()
async def test_a_list_counted_by_rows_may_repeat_an_item() -> None:
    fetch = _Pages(600, counts_rows=True)
    fetch.rows[300] = fetch.rows[299]
    walk = await read_wanted(fetch)
    assert (walk.complete, len(walk.items)) == (True, 600)


@pytest.mark.asyncio()
async def test_a_list_counted_by_rows_still_sees_its_total_move() -> None:
    fetch = _Pages(600, counts_rows=True)
    fetch.before[(2, 250)] = lambda rows: rows.pop(249)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, True)


@pytest.mark.asyncio()
async def test_a_tie_split_in_a_list_counted_by_rows_goes_unseen() -> None:
    """Known residual: a repeated row reads as a second file of the same book."""
    fetch = _Pages(600, counts_rows=True)
    fetch.views[(2, 250)] = _swap(249, 250)
    walk = await read_wanted(fetch)
    assert walk.complete
    assert 250 not in walk.items


@pytest.mark.asyncio()
async def test_one_leave_and_one_join_in_the_same_gap_go_unseen() -> None:
    """Known residual: the total never moves, so offset paging cannot see the skip."""
    fetch = _Pages(600)

    def leave_join(rows: list[int]) -> None:
        rows.pop(249)
        rows.append(9_999)

    fetch.before[(2, 250)] = leave_join
    walk = await read_wanted(fetch)
    assert walk.complete
    assert 250 not in walk.items


@pytest.mark.asyncio()
async def test_without_totals_the_short_page_decides() -> None:
    fetch = _Pages(600, totals=False)
    fetch.before[(2, 250)] = lambda rows: rows.pop(249)
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (True, False)


@pytest.mark.asyncio()
async def test_a_zero_total_beside_records_is_churned() -> None:
    fetch = _Pages(10)
    fetch.total_override = 0
    walk = await read_wanted(fetch)
    assert (walk.complete, walk.churned) == (False, True)


@pytest.mark.asyncio()
async def test_a_memo_serves_the_second_walk_without_fetching() -> None:
    fetch = _Pages(300)
    walks: WantedWalks = {}
    first = await walk_wanted(walks, "missing", fetch)
    second = await walk_wanted(walks, "missing", fetch)
    assert second is first
    assert fetch.calls == [(1, 1), (1, 250), (2, 250)]


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
    assert fetch.calls == [(1, 1), (1, 250), (1, 1), (1, 250)]


@pytest.mark.asyncio()
async def test_walk_wanted_asks_for_its_own_kind() -> None:
    kinds: list[str] = []
    fetch = _Pages(10)

    async def spy(kind: str, *, page: int, page_size: int) -> WantedPage[int]:
        kinds.append(kind)
        return await fetch(kind, page=page, page_size=page_size)

    await walk_wanted(None, "cutoff", spy)
    assert set(kinds) == {"cutoff"}


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("module", "itype"),
    [
        (sonarr_adapter, InstanceType.sonarr),
        (radarr_adapter, InstanceType.radarr),
        (lidarr_adapter, InstanceType.lidarr),
        (readarr_adapter, InstanceType.readarr),
        (whisparr_v2_adapter, InstanceType.whisparr_v2),
    ],
    ids=["sonarr", "radarr", "lidarr", "readarr", "whisparr_v2"],
)
@pytest.mark.parametrize("churned", ["missing", "cutoff"])
async def test_a_list_whose_reads_did_not_add_up_keeps_only_its_own_pass(
    module: Any, itype: InstanceType, churned: str
) -> None:
    walks: WantedWalks = {
        "missing": WantedWalk([], complete=churned != "missing", churned=churned == "missing"),
        "cutoff": WantedWalk([], complete=churned != "cutoff", churned=churned == "cutoff"),
    }

    sets = await module.fetch_reconcile_sets(AsyncMock(), make_instance(itype=itype), walks=walks)

    steady = "cutoff" if churned == "missing" else "missing"
    assert getattr(sets, churned) is None
    assert getattr(sets, steady) == frozenset()
    assert sets.upgrade == frozenset()

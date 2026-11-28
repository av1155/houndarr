"""Offset writes from a running cycle never undo a reset or an Edit save made during it.

A cycle reads its :class:`Instance` before it searches and writes the
advanced offsets at the end of each pass.  These cases run real cycles
against respx stubs and land a reset, a save, a delete or a second cycle
right before one of those writes.  The Edit save has the same lost update
on its own: it reads the row before its connection check, so it must not
write back the enabled flag it read.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from houndarr.engine import search_loop
from houndarr.engine.search_loop import run_instance_search
from houndarr.services import instance_submit
from houndarr.services.admin import reset_all_instance_policy
from houndarr.services.instance_submit import submit_update
from houndarr.services.instance_validation import API_KEY_UNCHANGED
from houndarr.services.instances import (
    Instance,
    InstanceType,
    SearchOrder,
    create_instance,
    delete_instance,
    get_instance,
    update_instance,
)

from .conftest import _COMMAND_RESP, _EPISODE_RECORD, MASTER_KEY, SONARR_URL, stored_offsets

_SERIES_COUNT = 10
_DEFAULTS = {
    "missing_page_offset": 1,
    "cutoff_page_offset": 1,
    "upgrade_item_offset": 0,
    "upgrade_series_offset": 0,
}
_OFF_DEFAULTS = {
    "missing_page_offset": 3,
    "cutoff_page_offset": 4,
    "upgrade_item_offset": 7,
    "upgrade_series_offset": 10,
}
# One cycle from _OFF_DEFAULTS: each page holds one record, so both page
# passes move one page on; the upgrade pool is series 1-5, so item 7 % 5
# is searched and the item offset becomes 3; the series offset moves by
# the default window of 5.
_ADVANCED = {
    "missing_page_offset": 4,
    "cutoff_page_offset": 5,
    "upgrade_item_offset": 3,
    "upgrade_series_offset": 15,
}


@pytest.fixture(autouse=True)
def _no_remote_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass the Edit save's connection check without a network call."""
    monkeypatch.setattr(instance_submit, "_verify_remote", AsyncMock(return_value=None))


def _wanted_pages(base_id: int) -> Callable[[httpx.Request], httpx.Response]:
    """Serve one record on every page, so a pass from page N searches it and moves to N + 1."""

    def respond(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        record = {**_EPISODE_RECORD, "id": base_id + page}
        return httpx.Response(
            200,
            json={"page": page, "pageSize": 10, "totalRecords": 1, "records": [record]},
        )

    return respond


def _episodes(request: httpx.Request) -> httpx.Response:
    series_id = int(request.url.params["seriesId"])
    return httpx.Response(
        200,
        json=[
            {
                "id": 5000 + series_id,
                "seriesId": series_id,
                "title": "Ep",
                "seasonNumber": 1,
                "episodeNumber": 1,
                "monitored": True,
                "hasFile": True,
                "episodeFile": {"qualityCutoffNotMet": False},
                "series": {"id": series_id, "title": f"Show {series_id}"},
            },
        ],
    )


def _mock_sonarr() -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(side_effect=_wanted_pages(1000))
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(side_effect=_wanted_pages(2000))
    respx.get(f"{SONARR_URL}/api/v3/series").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": n, "monitored": True, "title": f"Show {n}"}
                for n in range(1, _SERIES_COUNT + 1)
            ],
        ),
    )
    respx.get(f"{SONARR_URL}/api/v3/episode").mock(side_effect=_episodes)
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


async def _instance(**offsets: int) -> Instance:
    """Create a Sonarr instance with all three passes on in Chronological order."""
    created = await create_instance(
        master_key=MASTER_KEY,
        name="Race Sonarr",
        type=InstanceType.sonarr,
        url=SONARR_URL,
        api_key="test-api-key",
        batch_size=1,
        hourly_cap=0,
        cutoff_enabled=True,
        cutoff_batch_size=1,
        cutoff_hourly_cap=0,
        upgrade_enabled=True,
        upgrade_batch_size=1,
        upgrade_hourly_cap=0,
        search_order=SearchOrder.chronological,
    )
    if offsets:
        await update_instance(created.core.id, master_key=MASTER_KEY, **offsets)
    return await _snapshot(created.core.id)


async def _snapshot(instance_id: int) -> Instance:
    instance = await get_instance(instance_id, master_key=MASTER_KEY)
    assert instance is not None
    return instance


async def _reset() -> None:
    await reset_all_instance_policy(master_key=MASTER_KEY, supervisor=None)


async def _save(instance_id: int, **changes: Any) -> None:
    """Save the Edit form for *instance_id* as it is stored, with *changes* applied."""
    current = await _snapshot(instance_id)
    form: dict[str, Any] = {
        "name": current.core.name,
        "type": current.core.type.value,
        "url": current.core.url,
        "api_key": API_KEY_UNCHANGED,
        "missing_enabled": current.missing.missing_enabled,
        "batch_size": current.missing.batch_size,
        "sleep_interval_mins": current.missing.sleep_interval_mins,
        "hourly_cap": current.missing.hourly_cap,
        "cooldown_days": current.missing.cooldown_days,
        "post_release_grace_hrs": current.missing.post_release_grace_hrs,
        "missing_hot_retry_window_hrs": current.missing.missing_hot_retry_window_hrs,
        "missing_hot_retry_interval_hrs": current.missing.missing_hot_retry_interval_hrs,
        "queue_limit": current.missing.queue_limit,
        "cutoff_enabled": current.cutoff.cutoff_enabled,
        "cutoff_batch_size": current.cutoff.cutoff_batch_size,
        "cutoff_cooldown_days": current.cutoff.cutoff_cooldown_days,
        "cutoff_hourly_cap": current.cutoff.cutoff_hourly_cap,
        "sonarr_search_mode": current.missing.sonarr_search_mode.value,
        "lidarr_search_mode": current.missing.lidarr_search_mode.value,
        "readarr_search_mode": current.missing.readarr_search_mode.value,
        "whisparr_v2_search_mode": current.missing.whisparr_v2_search_mode.value,
        "upgrade_enabled": current.upgrade.upgrade_enabled,
        "upgrade_batch_size": current.upgrade.upgrade_batch_size,
        "upgrade_cooldown_days": current.upgrade.upgrade_cooldown_days,
        "upgrade_hourly_cap": current.upgrade.upgrade_hourly_cap,
        "upgrade_sonarr_search_mode": current.upgrade.upgrade_sonarr_search_mode.value,
        "upgrade_lidarr_search_mode": current.upgrade.upgrade_lidarr_search_mode.value,
        "upgrade_readarr_search_mode": current.upgrade.upgrade_readarr_search_mode.value,
        "upgrade_whisparr_v2_search_mode": current.upgrade.upgrade_whisparr_v2_search_mode.value,
        "upgrade_series_window_size": current.upgrade.upgrade_series_window_size,
        "allowed_time_window": current.schedule.allowed_time_window,
        "search_order": current.schedule.search_order.value,
        "tag_filter_include": "",
        "tag_filter_exclude": "",
        "tag_filter_missing_exclude": "",
        "tag_filter_cutoff_exclude": "",
        "tag_filter_upgrade_exclude": "",
        "connection_verified": True,
    }
    form.update(changes)
    await submit_update(instance_id, master_key=MASTER_KEY, **form)


def _written_column(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Name the column an offset write targets, whichever helper signature is in use."""
    if len(args) > 1 and isinstance(args[1], str):
        return args[1]
    return next(key for key in kwargs if key.endswith("_offset"))


def _before_write(
    monkeypatch: pytest.MonkeyPatch,
    action: Callable[[], Awaitable[object]],
    *,
    column: str | None = None,
) -> None:
    """Run *action* once, right before the cycle's first offset write or its write to *column*."""
    original = search_loop._persist_offset_with_typed_wrap
    pending = True

    async def persist(*args: Any, **kwargs: Any) -> None:
        nonlocal pending
        if pending and (column is None or _written_column(args, kwargs) == column):
            pending = False
            await action()
        await original(*args, **kwargs)

    monkeypatch.setattr(search_loop, "_persist_offset_with_typed_wrap", persist)


# ---------------------------------------------------------------------------
# No interference
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_cycle_alone_advances_every_offset(db: None) -> None:
    """Control for the cases below: each offset this cycle writes moves on."""
    _mock_sonarr()
    instance = await _instance(**_OFF_DEFAULTS)

    await run_instance_search(instance, MASTER_KEY)

    assert await stored_offsets(instance.core.id) == _ADVANCED


@respx.mock
async def test_an_item_offset_stored_past_the_pool_keeps_advancing(db: None) -> None:
    """The write compares the stored item offset, not its position in a smaller pool."""
    _mock_sonarr()
    instance = await _instance(upgrade_item_offset=12)

    await run_instance_search(instance, MASTER_KEY)
    assert (await stored_offsets(instance.core.id))["upgrade_item_offset"] == 3

    await run_instance_search(await _snapshot(instance.core.id), MASTER_KEY)
    assert (await stored_offsets(instance.core.id))["upgrade_item_offset"] == 4


# ---------------------------------------------------------------------------
# Reset all instance settings during a cycle
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("starts", "column"),
    [
        pytest.param(_OFF_DEFAULTS, None, id="before-every-write"),
        pytest.param(_DEFAULTS, None, id="before-every-write-from-defaults"),
        pytest.param(_OFF_DEFAULTS, "cutoff_page_offset", id="between-missing-and-cutoff"),
        pytest.param(_OFF_DEFAULTS, "upgrade_item_offset", id="between-series-and-item"),
    ],
)
async def test_a_reset_during_a_cycle_keeps_its_defaults(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    starts: dict[str, int],
    column: str | None,
) -> None:
    """The cycle's later writes leave the reset's offsets, even when it started at them."""
    _mock_sonarr()
    instance = await _instance(**starts)
    _before_write(monkeypatch, _reset, column=column)

    await run_instance_search(instance, MASTER_KEY)

    assert await stored_offsets(instance.core.id) == _DEFAULTS


# ---------------------------------------------------------------------------
# Edit save during a cycle
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_save_during_a_cycle_keeps_its_page_offsets(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The save's page 1 stands; the upgrade offsets the save left alone still advance."""
    _mock_sonarr()
    instance = await _instance(**_OFF_DEFAULTS)
    _before_write(monkeypatch, lambda: _save(instance.core.id))

    await run_instance_search(instance, MASTER_KEY)

    assert await stored_offsets(instance.core.id) == {
        "missing_page_offset": 1,
        "cutoff_page_offset": 1,
        "upgrade_item_offset": _ADVANCED["upgrade_item_offset"],
        "upgrade_series_offset": _ADVANCED["upgrade_series_offset"],
    }


@respx.mock
@pytest.mark.parametrize(
    ("item", "series"),
    [pytest.param(7, 10, id="from-moved-offsets"), pytest.param(0, 0, id="from-zero")],
)
async def test_turning_upgrade_off_during_a_cycle_keeps_its_offsets_at_zero(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    item: int,
    series: int,
) -> None:
    """The upgrade offsets the save zeroed stay at zero after the cycle's upgrade pass."""
    _mock_sonarr()
    instance = await _instance(upgrade_item_offset=item, upgrade_series_offset=series)
    _before_write(monkeypatch, lambda: _save(instance.core.id, upgrade_enabled=False))

    await run_instance_search(instance, MASTER_KEY)

    stored = await stored_offsets(instance.core.id)
    assert (stored["upgrade_item_offset"], stored["upgrade_series_offset"]) == (0, 0)


@respx.mock
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param(
            {"search_order": "random"},
            {
                "missing_page_offset": 1,
                "cutoff_page_offset": 1,
                "upgrade_item_offset": 7,
                "upgrade_series_offset": 15,
            },
            id="switch-to-random",
        ),
        pytest.param({"missing_enabled": False}, {"missing_page_offset": 1}, id="missing-off"),
        pytest.param({"cutoff_enabled": False}, {"cutoff_page_offset": 1}, id="cutoff-off"),
    ],
)
async def test_a_save_that_stops_a_pass_offset_keeps_the_saved_value(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    expected: dict[str, int],
) -> None:
    """From page 1 the values match, so only the stored mode stops the cycle's write."""
    _mock_sonarr()
    instance = await _instance(upgrade_item_offset=7, upgrade_series_offset=10)
    _before_write(monkeypatch, lambda: _save(instance.core.id, **change))

    await run_instance_search(instance, MASTER_KEY)

    stored = await stored_offsets(instance.core.id)
    assert {column: stored[column] for column in expected} == expected


@respx.mock
async def test_a_save_during_its_connection_check_keeps_a_disable_made_meanwhile(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disable committed during the save's connection check survives the save."""
    instance = await _instance()

    async def disable_during_check(*_args: Any, **_kwargs: Any) -> None:
        await update_instance(instance.core.id, master_key=MASTER_KEY, enabled=False)

    monkeypatch.setattr(instance_submit, "_verify_remote", disable_during_check)

    await _save(instance.core.id, batch_size=3)

    saved = await _snapshot(instance.core.id)
    assert saved.missing.batch_size == 3
    assert saved.core.enabled is False


# ---------------------------------------------------------------------------
# Stale snapshots and deleted rows
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_cycle_on_a_stale_snapshot_never_moves_an_offset_back(db: None) -> None:
    """A cycle queued behind two others writes nothing over their offsets."""
    _mock_sonarr()
    queued = await _instance(**_OFF_DEFAULTS)
    await run_instance_search(queued, MASTER_KEY)
    await run_instance_search(await _snapshot(queued.core.id), MASTER_KEY)
    after_two = await stored_offsets(queued.core.id)

    await run_instance_search(queued, MASTER_KEY)

    assert await stored_offsets(queued.core.id) == after_two


@respx.mock
async def test_a_row_deleted_during_a_cycle_ends_it_quietly(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The last write finds no row: no error, no warning, and nothing comes back."""
    _mock_sonarr()
    instance = await _instance(**_OFF_DEFAULTS)
    _before_write(
        monkeypatch,
        lambda: delete_instance(instance.core.id),
        column="upgrade_item_offset",
    )

    with caplog.at_level(logging.DEBUG, logger="houndarr.engine.search_loop"):
        await run_instance_search(instance, MASTER_KEY)

    assert await get_instance(instance.core.id, master_key=MASTER_KEY) is None
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []

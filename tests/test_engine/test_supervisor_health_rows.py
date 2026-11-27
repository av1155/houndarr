"""An instance's health rows follow what its cycles actually saw.

The dashboard and the settings page both mark an instance failing while
its newest ``search_log`` row is an error.  These tests drive the real
``Supervisor._run_search_cycle`` and ``_instance_loop`` over scripted
cycle results and check that the newest row tells the truth after each
sequence (issues #908 and #911).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from houndarr.clients.base import InstanceSnapshot, ReconcileSets
from houndarr.database import get_db
from houndarr.engine import supervisor as supervisor_module
from houndarr.engine.supervisor import Supervisor
from houndarr.errors import ClientHTTPError
from houndarr.repositories.search_log import fetch_active_error_instance_ids, insert_log_row
from houndarr.services.metrics import gather_active_errors
from tests.test_engine.conftest import MASTER_KEY, get_log_rows, make_instance

INSTANCE = make_instance(instance_id=1)
OUTAGE = f"Could not reach {INSTANCE.core.url}"
REACHABLE = f"{INSTANCE.core.name!r} ({INSTANCE.core.url}) is reachable again"
HEALTHY = f"{INSTANCE.core.name!r} is healthy again"
REJECTED = "get_tags: HTTP 401 from /api/v3/tag"
POOL_REJECTED = "upgrade pool fetch failed: HTTP 401 from /api/v3/series"
QUEUE_FULL = "Download queue has 10 items, limit is 5"
STILL_FAILING = f"{REACHABLE}; its last error is not rechecked yet: {POOL_REJECTED}"
HOURLY_CAP = "hourly limit reached"

Step = Callable[..., Awaitable[int]]


def _mark(kwargs: dict[str, Any], **fields: bool) -> None:
    report = kwargs.get("report")
    if report is None:
        return
    for name, value in fields.items():
        setattr(report, name, value)


async def _write(action: str, *, cycle_id: str | None, message: str | None = None) -> None:
    searched = action == "searched"
    await insert_log_row(
        instance_id=INSTANCE.core.id,
        item_id=101 if searched else None,
        item_type="episode" if searched else None,
        action=action,
        search_kind="missing" if searched else None,
        cycle_id=cycle_id,
        cycle_trigger="scheduled",
        message=message,
    )


async def unreachable(*_: Any, **__: Any) -> int:
    raise httpx.ConnectError("refused")


async def rejected(*_: Any, **__: Any) -> int:
    raise ClientHTTPError(REJECTED)


async def searched(*_: Any, **kwargs: Any) -> int:
    _mark(kwargs, reached=True)
    await _write("searched", cycle_id=kwargs["cycle_id"])
    return 1


async def quiet(*_: Any, **kwargs: Any) -> int:
    """A cycle that read the *arr's lists and found nothing to search."""
    _mark(kwargs, reached=True)
    return 0


async def window_skip(*_: Any, **__: Any) -> int:
    """A scheduled cycle outside the allowed window over an open error: no request, no row."""
    return 0


async def backpressure(*_: Any, **kwargs: Any) -> int:
    """The queue status answered over the limit, so no pass ran.

    Its info row is written only when the newest row is not an error.
    """
    _mark(kwargs, reached=True, passes_skipped=True)
    rows = await get_log_rows()
    if not rows or rows[-1]["action"] != "error":
        await _write("info", cycle_id=kwargs["cycle_id"], message=QUEUE_FULL)
    return 0


async def deferred(*_: Any, **kwargs: Any) -> int:
    """The upgrade list read timed out and nothing else answered."""
    _mark(kwargs, upgrade_deferred=True)
    return 0


async def deferred_after_contact(*_: Any, **kwargs: Any) -> int:
    """The upgrade list answered 503, which proves the *arr is up."""
    _mark(kwargs, reached=True, upgrade_deferred=True)
    return 0


async def deferred_after_a_pass(*_: Any, **kwargs: Any) -> int:
    """The missing pass read its list and logged a skip; the upgrade list answered 503."""
    _mark(kwargs, reached=True, upgrade_deferred=True)
    await _write("skipped", cycle_id=kwargs["cycle_id"], message=HOURLY_CAP)
    return 0


async def lost_mid_cycle(*_: Any, **kwargs: Any) -> int:
    """The missing pass searched, then the next request could not connect."""
    await searched(**kwargs)
    raise httpx.ConnectError("refused")


async def pool_rejected(*_: Any, **kwargs: Any) -> int:
    """The upgrade pass wrote its own error row and the cycle returned."""
    await _write("error", cycle_id=kwargs["cycle_id"], message=POOL_REJECTED)
    return 0


async def _messages() -> list[tuple[str, str | None]]:
    return [(r["action"], r["message"]) for r in await get_log_rows()]


async def _failing_on(*, settings: bool, dashboard: bool) -> bool:
    """Return whether both status surfaces agree with the expected state."""
    on_settings = INSTANCE.core.id in await fetch_active_error_instance_ids()
    async with get_db() as conn:
        on_dashboard = INSTANCE.core.id in await gather_active_errors(conn, [INSTANCE.core.id])
    return (on_settings, on_dashboard) == (settings, dashboard)


async def _cycle(sup: Supervisor, step: Step, trigger: str = "scheduled") -> None:
    with patch.object(supervisor_module, "run_instance_search", side_effect=step):
        await sup._run_search_cycle(INSTANCE, cycle_trigger=trigger)


async def _loop(sup: Supervisor, steps: list[Step]) -> list[float]:
    """Run ``_instance_loop`` over *steps*, then stop it; return its sleeps."""
    queue = list(steps)
    sleeps: list[float] = []

    async def run(*args: Any, **kwargs: Any) -> int:
        if not queue:
            raise asyncio.CancelledError
        return await queue.pop(0)(*args, **kwargs)

    async def sleep(secs: float) -> None:
        sleeps.append(secs)

    with (
        patch.object(supervisor_module, "_STARTUP_GRACE_SECS", 0),
        patch.object(supervisor_module, "get_instance", AsyncMock(return_value=INSTANCE)),
        patch.object(supervisor_module.asyncio, "sleep", side_effect=sleep),
        patch.object(supervisor_module, "run_instance_search", side_effect=run),
        pytest.raises(asyncio.CancelledError),
    ):
        await sup._instance_loop(INSTANCE.core.id)
    return sleeps[1:]


async def _snapshot(sup: Supervisor) -> None:
    class _Client:
        async def __aenter__(self) -> AsyncMock:
            return AsyncMock()

        async def __aexit__(self, *_: object) -> None:
            return None

    adapter = type(
        "Adapter",
        (),
        {
            "make_client": staticmethod(lambda _inst: _Client()),
            "fetch_instance_snapshot": staticmethod(
                AsyncMock(return_value=InstanceSnapshot(monitored_total=1, unreleased_count=0))
            ),
            "fetch_reconcile_sets": staticmethod(AsyncMock(return_value=ReconcileSets.empty())),
        },
    )()
    with patch.object(supervisor_module, "get_adapter", return_value=adapter):
        await sup._refresh_one_snapshot(INSTANCE)


@pytest.mark.asyncio()
async def test_rejection_after_an_outage_keeps_the_instance_failing(
    seeded_instances: None,
) -> None:
    """Issue #908: a 401 after an outage must not be followed by a recovery row."""
    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, rejected])

    assert await _messages() == [("error", OUTAGE), ("error", REJECTED)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_recovery_after_an_outage_closes_it_after_the_cycles_rows(
    seeded_instances: None,
) -> None:
    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, searched])

    assert await _messages() == [("error", OUTAGE), ("searched", None), ("info", REACHABLE)]
    assert await _failing_on(settings=False, dashboard=False)


@pytest.mark.asyncio()
async def test_an_outage_after_a_failure_writes_its_own_row(seeded_instances: None) -> None:
    await _loop(Supervisor(master_key=MASTER_KEY), [rejected, unreachable, unreachable])

    assert await _messages() == [("error", REJECTED), ("error", OUTAGE)]


@pytest.mark.asyncio()
async def test_a_quiet_cycle_after_a_failure_clears_it(seeded_instances: None) -> None:
    """A healthy cycle on an empty backlog writes nothing else, so it must close the error.

    Without the closing row the card stays offline, with Run now disabled,
    until some cycle happens to write a row.
    """
    await _loop(Supervisor(master_key=MASTER_KEY), [rejected, quiet, quiet])

    assert await _messages() == [("error", REJECTED), ("info", HEALTHY)]
    assert await _failing_on(settings=False, dashboard=False)


@pytest.mark.asyncio()
async def test_an_outage_resuming_after_a_snapshot_close_is_logged_again(
    seeded_instances: None,
) -> None:
    sup = Supervisor(master_key=MASTER_KEY)
    await _cycle(sup, unreachable, trigger="run_now")
    await _snapshot(sup)
    await _cycle(sup, unreachable)

    assert await _messages() == [("error", OUTAGE), ("info", REACHABLE), ("error", OUTAGE)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_a_restart_closes_an_open_outage_row_without_repeating_it(
    seeded_instances: None,
) -> None:
    await _write("error", cycle_id=None, message=OUTAGE)

    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, quiet])

    assert await _messages() == [("error", OUTAGE), ("info", REACHABLE)]


@pytest.mark.asyncio()
async def test_a_purged_outage_row_is_written_again(seeded_instances: None) -> None:
    async def purge_then_unreachable(*args: Any, **kwargs: Any) -> int:
        async with get_db() as conn:
            await conn.execute("DELETE FROM search_log")
            await conn.commit()
        return await unreachable(*args, **kwargs)

    await _loop(
        Supervisor(master_key=MASTER_KEY), [unreachable, purge_then_unreachable, unreachable]
    )

    assert await _messages() == [("error", OUTAGE)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_a_cycle_that_sends_no_request_claims_no_recovery(seeded_instances: None) -> None:
    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, window_skip])

    assert await _messages() == [("error", OUTAGE)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_a_backpressure_skip_ends_an_outage_but_no_other_failure(
    seeded_instances: None,
) -> None:
    """A queue status read proves the *arr is up, not that a 401 seen before is fixed."""
    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, backpressure, backpressure])
    assert await _messages() == [("error", OUTAGE), ("info", REACHABLE), ("info", QUEUE_FULL)]
    assert await _failing_on(settings=False, dashboard=False)

    async with get_db() as conn:
        await conn.execute("DELETE FROM search_log")
        await conn.commit()
    await _loop(Supervisor(master_key=MASTER_KEY), [rejected, backpressure, backpressure])
    assert await _messages() == [("error", REJECTED)]
    assert await _failing_on(settings=True, dashboard=True)


async def _close_by_backpressure(sup: Supervisor) -> None:
    await _cycle(sup, backpressure)


async def _close_by_deferred_list(sup: Supervisor) -> None:
    await _cycle(sup, deferred_after_contact)


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "close",
    [_close_by_backpressure, _close_by_deferred_list, _snapshot],
    ids=["backpressure", "deferred-list", "snapshot"],
)
async def test_an_outage_closed_without_a_clean_pass_keeps_the_error_it_covered(
    seeded_instances: None, close: Callable[[Supervisor], Awaitable[None]]
) -> None:
    """The reconnect proves the *arr answers, not that the 401 from before the outage is fixed."""
    sup = Supervisor(master_key=MASTER_KEY)
    await _cycle(sup, pool_rejected)
    await _cycle(sup, unreachable)
    await close(sup)
    await _cycle(sup, window_skip)

    assert await _messages() == [
        ("error", POOL_REJECTED),
        ("error", OUTAGE),
        ("error", STILL_FAILING),
    ]
    assert await _failing_on(settings=True, dashboard=True)

    await _cycle(sup, quiet)
    assert (await _messages())[-1] == ("info", HEALTHY)
    assert await _failing_on(settings=False, dashboard=False)


@pytest.mark.asyncio()
async def test_a_carried_error_is_not_nested_across_outages(seeded_instances: None) -> None:
    sup = Supervisor(master_key=MASTER_KEY)
    for step in (pool_rejected, unreachable, backpressure, unreachable, backpressure):
        await _cycle(sup, step)

    assert await _messages() == [
        ("error", POOL_REJECTED),
        ("error", OUTAGE),
        ("error", STILL_FAILING),
        ("error", OUTAGE),
        ("error", STILL_FAILING),
    ]


@pytest.mark.asyncio()
async def test_pass_output_over_an_outage_is_not_covered_by_a_carried_error(
    seeded_instances: None,
) -> None:
    """The cycle's own pass rows are newer than the error the outage covered."""
    sup = Supervisor(master_key=MASTER_KEY)
    for step in (pool_rejected, unreachable, deferred_after_a_pass):
        await _cycle(sup, step)

    assert await _messages() == [
        ("error", POOL_REJECTED),
        ("error", OUTAGE),
        ("skipped", HOURLY_CAP),
        ("info", REACHABLE),
    ]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "close",
    [_close_by_backpressure, _close_by_deferred_list, _snapshot],
    ids=["backpressure", "deferred-list", "snapshot"],
)
async def test_an_outage_over_a_healthy_instance_closes_plainly(
    seeded_instances: None, close: Callable[[Supervisor], Awaitable[None]]
) -> None:
    sup = Supervisor(master_key=MASTER_KEY)
    await _cycle(sup, searched)
    await _cycle(sup, unreachable)
    await close(sup)

    assert await _messages() == [("searched", None), ("error", OUTAGE), ("info", REACHABLE)]
    assert await _failing_on(settings=False, dashboard=False)


@pytest.mark.asyncio()
async def test_adjacent_outage_rows_close_plainly(seeded_instances: None) -> None:
    """Two outage rows in a row (left by a failed dedupe read) carry nothing forward."""
    await _write("error", cycle_id=None, message=OUTAGE)
    await _write("error", cycle_id=None, message=OUTAGE)

    await _cycle(Supervisor(master_key=MASTER_KEY), backpressure)

    assert (await _messages())[-1] == ("info", REACHABLE)


@pytest.mark.asyncio()
async def test_an_unreadable_covered_row_leaves_the_outage_open(seeded_instances: None) -> None:
    """A read hiccup must not let the reconnect clear an error it could not see."""
    sup = Supervisor(master_key=MASTER_KEY)
    await _cycle(sup, pool_rejected)
    await _cycle(sup, unreachable)
    broken = AsyncMock(side_effect=sqlite3.OperationalError("database is locked"))
    with patch.object(supervisor_module, "fetch_log_row_before_newest", broken):
        await _cycle(sup, backpressure)
    assert await _messages() == [("error", POOL_REJECTED), ("error", OUTAGE)]

    await _cycle(sup, backpressure)
    assert (await _messages())[-1] == ("error", STILL_FAILING)


@pytest.mark.asyncio()
async def test_a_clean_cycle_closes_an_outage_and_the_error_it_covered(
    seeded_instances: None,
) -> None:
    await _loop(Supervisor(master_key=MASTER_KEY), [rejected, unreachable, searched])

    assert await _messages() == [
        ("error", REJECTED),
        ("error", OUTAGE),
        ("searched", None),
        ("info", REACHABLE),
    ]
    assert await _failing_on(settings=False, dashboard=False)


@pytest.mark.asyncio()
@pytest.mark.parametrize("step", [deferred, deferred_after_contact], ids=["alone", "after-contact"])
async def test_a_deferred_upgrade_list_keeps_a_failure_open(
    seeded_instances: None, step: Step
) -> None:
    """A 503 proves the *arr answers, not that an earlier failure is fixed."""
    await _loop(Supervisor(master_key=MASTER_KEY), [rejected, step])

    assert await _messages() == [("error", REJECTED)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_a_deferred_cycle_that_reached_the_arr_ends_an_outage(
    seeded_instances: None,
) -> None:
    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, deferred_after_contact])

    assert await _messages() == [("error", OUTAGE), ("info", REACHABLE)]


@pytest.mark.asyncio()
async def test_a_deferred_cycle_nothing_answered_keeps_the_outage_and_its_retry(
    seeded_instances: None,
) -> None:
    sleeps = await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, deferred])

    assert await _messages() == [("error", OUTAGE)]
    assert sleeps == [supervisor_module._CONNECT_RETRY_SECS] * 2


@pytest.mark.asyncio()
async def test_an_outage_mid_cycle_is_logged_after_the_cycles_rows(
    seeded_instances: None,
) -> None:
    await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, lost_mid_cycle])

    assert await _messages() == [("error", OUTAGE), ("searched", None), ("error", OUTAGE)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_a_cycle_ending_on_its_own_error_row_stays_failing(seeded_instances: None) -> None:
    """Issue #911: an upgrade list the *arr refuses ends the cycle failing."""
    sleeps = await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, pool_rejected])

    assert await _messages() == [("error", OUTAGE), ("error", POOL_REJECTED)]
    assert await _failing_on(settings=True, dashboard=True)
    assert sleeps == [
        supervisor_module._CONNECT_RETRY_SECS,
        INSTANCE.missing.sleep_interval_mins * 60,
    ]


@pytest.mark.asyncio()
async def test_a_snapshot_does_not_close_a_newer_failure(seeded_instances: None) -> None:
    """A refresh proves the *arr reachable, not that a 401 seen since is fixed."""
    sup = Supervisor(master_key=MASTER_KEY)
    await _cycle(sup, unreachable, trigger="run_now")
    await _cycle(sup, rejected)
    await _snapshot(sup)

    assert await _messages() == [("error", OUTAGE), ("error", REJECTED)]
    assert await _failing_on(settings=True, dashboard=True)


@pytest.mark.asyncio()
async def test_an_unreadable_log_keeps_the_loop_running(seeded_instances: None) -> None:
    """A database hiccup around the health rows is logged and the loop carries on."""
    broken = AsyncMock(side_effect=sqlite3.OperationalError("database is locked"))
    with patch.object(supervisor_module, "fetch_newest_log_row", broken):
        await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, quiet, rejected])

    assert await _messages() == [("error", OUTAGE), ("error", REJECTED)]


@pytest.mark.asyncio()
async def test_an_unwritable_log_keeps_the_loop_running(seeded_instances: None) -> None:
    """A database error writing any health row is logged and the loop carries on."""
    broken = AsyncMock(side_effect=sqlite3.OperationalError("database is locked"))
    with patch.object(supervisor_module, "_write_log", broken):
        sleeps = await _loop(Supervisor(master_key=MASTER_KEY), [unreachable, rejected, quiet])

    assert broken.await_count == 2
    assert len(sleeps) == 3
    assert await _messages() == []

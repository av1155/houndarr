"""Background supervisor: manages one asyncio.Task per enabled instance.

The supervisor is started once during application lifespan and runs until
shutdown.  Each task loops indefinitely: run a search cycle, sleep for
``sleep_interval_mins``, repeat.  Cancellation (on shutdown) is handled
gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from typing import Literal
from uuid import uuid4

import httpx

from houndarr.clients.base import ReconcileSets
from houndarr.database import get_db
from houndarr.engine.adapters import get_adapter
from houndarr.engine.adapters.protocols import AppAdapterProto
from houndarr.engine.retry import ReconnectState, run_with_reconnect
from houndarr.engine.search_loop import _write_log, run_instance_search
from houndarr.enums import CycleTrigger, SearchAction
from houndarr.errors import ClientError, EngineError
from houndarr.services.cooldown_reconcile import reconcile_cooldowns
from houndarr.services.instances import (
    Instance,
    get_instance,
    list_instances,
    update_instance_snapshot,
)

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT = 10  # seconds to wait for tasks to finish on stop()
_CONNECT_RETRY_SECS = 30  # back-off interval when a connection error occurs
_STARTUP_GRACE_SECS = 10  # one-time delay before the first cycle fires per instance
_STARTUP_STAGGER_SECS = 30  # per-instance offset added to startup grace at initial start()
_SNAPSHOT_REFRESH_INTERVAL_SECS = 600  # 10 minutes, matches the locked plan cadence
_SNAPSHOT_INITIAL_DELAY_SECS = 20  # first run after startup, gives arr time to come up
_UNRELEASED_DELTA_LOG_THRESHOLD = 10  # log INFO when |new - prior| exceeds this
RunNowStatus = Literal["accepted", "not_found", "disabled"]


async def _read_prior_unreleased(instance_id: int) -> int | None:
    """Return the currently-stored ``unreleased_count`` for an instance.

    A targeted SELECT keeps the delta-logging path off the
    decryption-heavy :func:`get_instance` helper (the only field this
    needs is one int).  Returns ``None`` when the row is missing so
    the caller can suppress the delta log on a freshly-deleted
    instance instead of treating "absent" as "0 -> N jumped".
    """
    async with get_db() as conn:
        async with conn.execute(
            "SELECT unreleased_count FROM instances WHERE id = ?",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return int(row["unreleased_count"])


class Supervisor:
    """Manages one background search task per enabled *arr instance.

    Usage (in FastAPI lifespan)::

        supervisor = Supervisor(master_key=app.state.master_key)
        await supervisor.start()
        app.state.supervisor = supervisor
        yield
        await supervisor.stop()
    """

    def __init__(self, master_key: bytes) -> None:
        self._master_key = master_key
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._manual_runs: dict[int, asyncio.Task[None]] = {}
        self._run_locks: dict[int, asyncio.Lock] = {}
        # Global snapshot-refresh task handle.  Populated in ``start`` and
        # cancelled in ``stop``.
        self._snapshot_task: asyncio.Task[None] | None = None
        # Fire-and-forget snapshot-prime tasks spawned on ``start_instance_task``.
        # Tracked so ``stop()`` can cancel anything still mid-flight when the
        # supervisor tears down (e.g. during a factory reset that wipes the DB
        # out from under an in-progress snapshot write).
        self._prime_tasks: set[asyncio.Task[None]] = set()
        # In-memory "last cycle ended at" timestamp per instance.  Populated
        # at the end of every _run_search_cycle invocation so the dashboard
        # countdown can anchor on a signal that always advances, even on
        # instances where every item is on cooldown and the LRU skip-log
        # throttle silences all skip rows (which would otherwise freeze
        # `last_activity_at` for hours and pin the countdown on "running...").
        # Lost on process restart; the dashboard falls back to
        # `last_activity_at` in that case until the first post-restart cycle
        # completes.
        self._last_cycle_end: dict[int, datetime] = {}

    # Lifecycle

    async def start(self) -> None:
        """Load enabled instances and launch one loop-task per instance."""
        instances = await list_instances(master_key=self._master_key)
        enabled = [i for i in instances if i.core.enabled]

        for idx, instance in enumerate(enabled):
            await self.start_instance_task(
                instance.core.id, instance=instance, startup_offset=idx * _STARTUP_STAGGER_SECS
            )

        # Snapshot refresh runs even when no search cycles are enabled, so
        # a fresh install with zero enabled instances still gets the
        # empty-state treatment right. Disabled instances skip the refresh
        # itself (``_refresh_all_snapshots_once`` filters on ``enabled``)
        # so their columns freeze at their last-known values until the
        # operator re-enables them.
        self._snapshot_task = asyncio.create_task(
            self._snapshot_refresh_loop(),
            name="snapshot-refresh-loop",
        )

        if not self._tasks:
            logger.warning("Supervisor: no enabled instances configured. Nothing to do.")
            return

        await _write_log(
            instance_id=None,
            item_id=None,
            item_type=None,
            action=SearchAction.info.value,
            cycle_trigger="system",
            message=f"Supervisor started {len(self._tasks)} task(s)",
        )

    async def stop(self) -> None:
        """Cancel all running tasks and wait up to 10 s for clean exit."""
        self._prune_scheduled_tasks()
        self._prune_manual_tasks()

        snapshot = self._snapshot_task
        if snapshot is not None and not snapshot.done():
            snapshot.cancel()
            with suppress(asyncio.CancelledError):
                await snapshot
        self._snapshot_task = None

        # Cancel any in-flight snapshot-prime tasks spawned from
        # ``start_instance_task`` so they don't write to the DB after the
        # supervisor has torn down (factory reset deletes the DB right after
        # this call returns).
        if self._prime_tasks:
            for prime in list(self._prime_tasks):
                prime.cancel()
            await asyncio.gather(*self._prime_tasks, return_exceptions=True)
            self._prime_tasks.clear()

        if not self._tasks and not self._manual_runs:
            return

        for task in self._manual_runs.values():
            task.cancel()

        for task in self._tasks.values():
            task.cancel()

        all_tasks = [*self._tasks.values(), *self._manual_runs.values()]

        done, pending = await asyncio.wait(
            all_tasks,
            timeout=_SHUTDOWN_TIMEOUT,
        )

        # Force-cancel anything that outlived the timeout
        for task in pending:
            task.cancel()
            logger.warning("Supervisor: task did not finish within timeout; force cancelled")

        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error("Supervisor: task raised unexpected exception: %s", exc)

        self._tasks.clear()
        self._manual_runs.clear()
        logger.info("Supervisor: all tasks stopped")

    async def start_instance_task(
        self, instance_id: int, *, instance: Instance | None = None, startup_offset: int = 0
    ) -> bool:
        """Ensure the scheduled loop task exists for *instance_id* when enabled."""
        self._prune_scheduled_tasks()

        existing = self._tasks.get(instance_id)
        if existing is not None and not existing.done():
            return False

        current = instance
        if current is None:
            current = await get_instance(instance_id, master_key=self._master_key)
        if current is None or not current.core.enabled:
            return False

        task = asyncio.create_task(
            self._instance_loop(instance_id, startup_offset=startup_offset),
            name=f"search-loop-{instance_id}",
        )
        task.add_done_callback(partial(self._on_scheduled_task_done, instance_id))
        self._tasks[instance_id] = task

        # Kick off an immediate one-shot snapshot refresh so a freshly-
        # added or re-enabled instance's dashboard counters (monitored /
        # unreleased) populate right away instead of sitting at zero for
        # up to 10 minutes waiting on the scheduled refresh loop.
        prime = asyncio.create_task(
            self._refresh_one_snapshot(current),
            name=f"snapshot-prime-{instance_id}",
        )
        self._prime_tasks.add(prime)
        prime.add_done_callback(self._prime_tasks.discard)

        logger.info(
            "Supervisor: started task for instance %r (id=%d)",
            current.core.name,
            current.core.id,
        )
        return True

    async def stop_instance_task(self, instance_id: int) -> bool:
        """Cancel scheduled and manual tasks for *instance_id*."""
        self._prune_scheduled_tasks()
        self._prune_manual_tasks()

        stopped = False

        scheduled = self._tasks.pop(instance_id, None)
        if scheduled is not None and not scheduled.done():
            scheduled.cancel()
            with suppress(asyncio.CancelledError):
                await scheduled
            stopped = True

        manual = self._manual_runs.pop(instance_id, None)
        if manual is not None and not manual.done():
            manual.cancel()
            with suppress(asyncio.CancelledError):
                await manual
            stopped = True

        return stopped

    async def reconcile_instance(self, instance_id: int) -> None:
        """Start or stop tasks to match the instance's current enabled state."""
        instance = await get_instance(instance_id, master_key=self._master_key)
        if instance is None or not instance.core.enabled:
            await self.stop_instance_task(instance_id)
            return

        await self.start_instance_task(instance_id, instance=instance)

    async def trigger_run_now(self, instance_id: int) -> RunNowStatus:
        """Queue one immediate search cycle for *instance_id* if active."""
        self._prune_manual_tasks()

        instance = await get_instance(instance_id, master_key=self._master_key)
        if instance is None:
            return "not_found"
        if not instance.core.enabled:
            return "disabled"

        existing = self._manual_runs.get(instance_id)
        if existing is not None and not existing.done():
            return "accepted"

        task = asyncio.create_task(
            self._run_manual_once(instance_id), name=f"run-now-{instance_id}"
        )
        task.add_done_callback(partial(self._on_manual_task_done, instance_id))
        self._manual_runs[instance_id] = task
        return "accepted"

    # Internal

    async def _instance_loop(self, instance_id: int, startup_offset: int = 0) -> None:
        """Run search cycles for one instance until cancelled.

        A one-time startup grace delay gives co-located *arr services time
        to become ready before the first cycle fires.  Connection errors are
        logged to ``search_log`` only on state transitions (first failure and
        recovery) to avoid inflating the dashboard error counter with retry
        noise; the state machine lives in
        :func:`houndarr.engine.retry.run_with_reconnect`.
        """
        logger.debug("Supervisor: loop started for instance id=%d", instance_id)

        logger.info(
            "Supervisor: waiting %d s startup grace for instance id=%d",
            _STARTUP_GRACE_SECS + startup_offset,
            instance_id,
        )
        await asyncio.sleep(_STARTUP_GRACE_SECS + startup_offset)

        state = ReconnectState()

        try:
            while True:
                instance = await get_instance(instance_id, master_key=self._master_key)
                if instance is None:
                    logger.warning(
                        "Supervisor: instance id=%d no longer exists; stopping loop",
                        instance_id,
                    )
                    return

                if not instance.core.enabled:
                    logger.info(
                        "Supervisor: instance %r disabled; stopping loop",
                        instance.core.name,
                    )
                    return

                sleep_secs = await run_with_reconnect(
                    state,
                    instance=instance,
                    cycle=partial(self._run_search_cycle, instance, cycle_trigger="scheduled"),
                    cycle_trigger="scheduled",
                    error_retry_secs=_CONNECT_RETRY_SECS,
                    success_sleep_secs=instance.missing.sleep_interval_mins * 60,
                    write_log=_write_log,
                )
                await asyncio.sleep(sleep_secs)

        except asyncio.CancelledError:
            logger.debug("Supervisor: loop cancelled for instance id=%d", instance_id)
            raise

    async def _run_manual_once(self, instance_id: int) -> None:
        """Run one ad-hoc cycle, serialized with the scheduled loop."""
        instance = await get_instance(instance_id, master_key=self._master_key)
        if instance is None or not instance.core.enabled:
            return

        await self._run_search_cycle(instance, cycle_trigger="run_now")

    async def _run_search_cycle(
        self, instance: Instance, *, cycle_trigger: CycleTrigger | str
    ) -> bool:
        """Run exactly one cycle for *instance* under the per-instance lock.

        Returns:
            ``True`` if the cycle failed with a connection error, ``False`` otherwise.

        Records the cycle-end wallclock in ``_last_cycle_end`` regardless
        of cycle outcome (success / transport error / engine error) so
        the dashboard countdown anchors on a signal that advances once
        per cycle even when every item is LRU-throttled.
        """
        lock = self._run_locks.setdefault(instance.core.id, asyncio.Lock())
        async with lock:
            cycle_id = str(uuid4())
            try:
                await run_instance_search(
                    instance,
                    self._master_key,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                )
                return False
            except httpx.TransportError:
                logger.warning(
                    "Supervisor: could not reach %r (%s); retrying in %d s",
                    instance.core.name,
                    instance.core.url,
                    _CONNECT_RETRY_SECS,
                )
                return True
            except (EngineError, ClientError) as exc:
                # ``run_instance_search`` wraps any non-typed escape in
                # :class:`EngineError` before it reaches this handler,
                # and the client layer raises :class:`ClientError`
                # subclasses directly, so both branches land on the
                # same ``search_log`` row shape.
                logger.error(
                    "Supervisor: unhandled error in search loop for %r: %s",
                    instance.core.name,
                    exc,
                )
                await _write_log(
                    instance_id=instance.core.id,
                    item_id=None,
                    item_type=None,
                    action=SearchAction.error.value,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    message=str(exc),
                )
                return False
            finally:
                self._last_cycle_end[instance.core.id] = datetime.now(UTC)

    def cycle_end_timestamps(self) -> dict[int, str]:
        """Return ISO-8601 UTC timestamps for each instance's most recent cycle end.

        Populated in memory by ``_run_search_cycle`` (see its finally
        block).  Consumed by the ``/api/status`` route so the dashboard
        countdown can anchor on a signal that advances once per cycle,
        including for instances whose every skip is LRU-throttled.

        Returns an empty dict if no cycle has completed since the
        supervisor started, so the caller can treat "missing" as the
        equivalent of "fall back to last_activity_at".
        """
        return {iid: ts.isoformat() for iid, ts in self._last_cycle_end.items()}

    async def _snapshot_refresh_loop(self) -> None:
        """Background loop: refresh dashboard snapshot columns at 10-min cadence.

        Keeps each enabled instance's ``monitored_total`` and
        ``unreleased_count`` columns fresh so ``/api/status?v=2`` can
        serve them without fanning out to arr on every poll.  Failures
        are non-fatal: an unreachable arr keeps its last-known snapshot
        and the loop moves on to the next instance.
        """
        try:
            await asyncio.sleep(_SNAPSHOT_INITIAL_DELAY_SECS)
        except asyncio.CancelledError:
            raise

        while True:
            try:
                await self._refresh_all_snapshots_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Supervisor: snapshot refresh iteration failed")

            try:
                await asyncio.sleep(_SNAPSHOT_REFRESH_INTERVAL_SECS)
            except asyncio.CancelledError:
                raise

    async def _refresh_one_snapshot(self, instance: Instance) -> None:
        """Refresh snapshot columns for a single enabled instance.

        Shared between the scheduled refresh loop and the one-shot prime
        fired on ``start_instance_task`` so a freshly-added instance's
        dashboard counters land in the next status poll instead of
        sitting at zero until the 10-minute loop wakes. Skips disabled
        instances and treats transport errors as soft failures.

        Also reconciles the ``cooldowns`` table for this instance
        against the authoritative wanted / upgrade-pool sets returned
        by the adapter.  Cooldown rows for items that have left the
        *arr's wanted state (downloaded, unmonitored, deleted,
        cutoff-met) are deleted in the same round-trip so the
        dashboard breakdown reflects live reality instead of
        historical search dispatches that no longer bind.  An adapter
        failure anywhere in the reconcile fetch causes the reconcile
        step to skip (the :meth:`ReconcileSets.empty` sentinel path),
        preserving existing rows rather than risking a wipe on a
        transient *arr blip.
        """
        if not instance.core.enabled:
            return
        lock = self._run_locks.setdefault(instance.core.id, asyncio.Lock())
        async with lock:
            try:
                adapter: AppAdapterProto = get_adapter(instance.core.type)
                async with adapter.make_client(instance) as client:
                    snap = await adapter.fetch_instance_snapshot(client, instance)
                    try:
                        reconcile_sets = await adapter.fetch_reconcile_sets(client, instance)
                    except httpx.TransportError:
                        reconcile_sets = ReconcileSets.empty()
                        logger.debug(
                            "Supervisor: reconcile sets unreachable for %r; "
                            "keeping existing cooldowns.",
                            instance.core.name,
                        )
                    except Exception:  # noqa: BLE001
                        reconcile_sets = ReconcileSets.empty()
                        logger.exception(
                            "Supervisor: reconcile fetch failed for %r; "
                            "keeping existing cooldowns.",
                            instance.core.name,
                        )
                # Surface a one-line INFO when the unreleased count
                # jumps non-trivially.  The first refresh after a user
                # upgrade flips every non-Whisparr-v3 instance from 0
                # to its real count; without this log the transition
                # is silent.  Threshold is intentionally noisy enough
                # to ignore +/- 1 churn from a single release going
                # past midnight.
                prior_unreleased = await _read_prior_unreleased(instance.core.id)
                if (
                    prior_unreleased is not None
                    and abs(snap.unreleased_count - prior_unreleased)
                    > _UNRELEASED_DELTA_LOG_THRESHOLD
                ):
                    logger.info(
                        "Supervisor: %r unreleased jumped %d -> %d",
                        instance.core.name,
                        prior_unreleased,
                        snap.unreleased_count,
                    )
                await update_instance_snapshot(
                    instance.core.id,
                    monitored_total=snap.monitored_total,
                    unreleased_count=snap.unreleased_count,
                )
                await reconcile_cooldowns(instance.core.id, reconcile_sets)
            except httpx.TransportError:
                logger.debug(
                    "Supervisor: snapshot refresh skipped for %r; instance unreachable",
                    instance.core.name,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Supervisor: snapshot refresh failed for %r", instance.core.name)

    async def _refresh_all_snapshots_once(self) -> None:
        """Refresh the snapshot columns for every enabled instance once.

        Runs sequentially; acquires the per-instance search lock before
        writing so it cannot race an in-progress cycle.  Disabled
        instances are skipped (their last-known snapshot stays on disk).
        """
        instances = await list_instances(master_key=self._master_key)
        for inst in instances:
            await self._refresh_one_snapshot(inst)

    def _on_scheduled_task_done(self, instance_id: int, task: asyncio.Task[None]) -> None:
        """Remove finished scheduled task references."""
        current = self._tasks.get(instance_id)
        if current is task:
            self._tasks.pop(instance_id, None)

        if task.cancelled():
            return

        exc = task.exception()
        if exc is not None:
            logger.error("Supervisor: scheduled task for id=%d failed: %s", instance_id, exc)

    def _on_manual_task_done(self, instance_id: int, task: asyncio.Task[None]) -> None:
        """Remove finished run-now task references."""
        current = self._manual_runs.get(instance_id)
        if current is task:
            self._manual_runs.pop(instance_id, None)

        if task.cancelled():
            return

        exc = task.exception()
        if exc is not None:
            logger.error("Supervisor: run-now task for id=%d failed: %s", instance_id, exc)

    def _prune_scheduled_tasks(self) -> None:
        """Drop references to done scheduled tasks."""
        done_ids = [instance_id for instance_id, task in self._tasks.items() if task.done()]
        for instance_id in done_ids:
            task = self._tasks.pop(instance_id)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.error("Supervisor: scheduled task for id=%d failed: %s", instance_id, exc)

    def _prune_manual_tasks(self) -> None:
        """Drop references to done manual run-now tasks."""
        done_ids = [instance_id for instance_id, task in self._manual_runs.items() if task.done()]
        for instance_id in done_ids:
            task = self._manual_runs.pop(instance_id)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.error("Supervisor: run-now task for id=%d failed: %s", instance_id, exc)

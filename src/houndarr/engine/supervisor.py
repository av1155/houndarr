"""Background supervisor: manages one asyncio.Task per enabled instance.

The supervisor is started once during application lifespan and runs until
shutdown.  Each task loops indefinitely: run a search cycle, sleep for
``sleep_interval_mins``, repeat.  Cancellation (on shutdown) is handled
gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime
from functools import partial
from typing import Literal
from uuid import uuid4

import httpx
from aiosqlitepool import PoolClosedError, PoolConnectionAcquireTimeoutError

from houndarr.clients.base import ReconcileSets, WantedKind, WantedWalks
from houndarr.database import get_db
from houndarr.engine.adapters import get_adapter
from houndarr.engine.adapters.protocols import AppAdapterProto
from houndarr.engine.retry import CycleOutcome, run_with_reconnect
from houndarr.engine.search_loop import CycleReport, _write_log, run_instance_search
from houndarr.enums import CycleTrigger, SearchAction
from houndarr.errors import ClientError, ClientTransportError, EngineError, redact_url_credentials
from houndarr.repositories.search_log import (
    NewestLogRow,
    fetch_newest_log_row,
    fetch_newest_log_row_under_errors,
)
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
_CHURN_WARN_AFTER = 6  # refreshes in a row, about an hour at the refresh cadence
_WANTED_KINDS: tuple[WantedKind, ...] = ("missing", "cutoff")
RunNowStatus = Literal["accepted", "not_found", "disabled", "key_unreadable"]
KEY_UNREADABLE_ERROR = (
    "API key can't be decrypted with the current master key; enter it again in Settings"
)
_UNREACHABLE_PREFIX = "Could not reach "
_NOT_RECHECKED = "; its last error is not rechecked yet: "
# Stands in for a row under an outage that could not be read.
_UNREADABLE_ROW = NewestLogRow(action="", cycle_id=None, message=None)
# A database hiccup around the health rows must not end an instance's loop.
_DB_ERRORS = (sqlite3.Error, PoolClosedError, PoolConnectionAcquireTimeoutError)


async def _cancel_and_wait(task: asyncio.Task[None]) -> None:
    task.cancel()
    # wait(), unlike suppress(), lets a cancel of this caller propagate.
    await asyncio.wait({task})


def _is_unreachable(row: NewestLogRow | None) -> bool:
    """Return whether *row* is an open ``Could not reach`` error."""
    return (
        row is not None
        and row.action == SearchAction.error.value
        and (row.message or "").startswith(_UNREACHABLE_PREFIX)
    )


def _is_key_error(row: NewestLogRow | None) -> bool:
    """Return whether *row* is the open unreadable-API-key error."""
    return (
        row is not None
        and row.action == SearchAction.error.value
        and row.message == KEY_UNREADABLE_ERROR
    )


def _reachable_again(instance: Instance) -> str:
    """Return the row that closes an open ``Could not reach`` error."""
    return f"{instance.core.name!r} ({instance.core.url}) is reachable again"


def _name_lists(names: list[str]) -> str:
    """Return ``"missing list"``, ``"missing and cutoff lists"`` and so on."""
    *head, last = names
    joined = f"{', '.join(head)} and {last}" if head else last
    return f"{joined} {'list' if len(names) == 1 else 'lists'}"


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
        app.state.supervisor = supervisor
        try:
            await supervisor.start()
            yield
        finally:
            await supervisor.stop()

    ``start()`` can raise after it has spawned tasks, so publish the
    supervisor first and stop it on every exit path.
    """

    def __init__(self, master_key: bytes) -> None:
        self._master_key = master_key
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._manual_runs: dict[int, asyncio.Task[None]] = {}
        self._run_locks: dict[int, asyncio.Lock] = {}
        # Orders an instance's unreadable-key error row against the row that
        # closes it; held only for those writes, never across a cycle.
        self._key_row_locks: dict[int, asyncio.Lock] = {}
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
        # instances where every item is on cooldown and the skip-log
        # throttle silences all skip rows (which would otherwise freeze
        # `last_activity_at` for hours and pin the countdown on "running...").
        # Lost on process restart; the dashboard falls back to
        # `last_activity_at` in that case until the first post-restart cycle
        # completes.
        self._last_cycle_end: dict[int, datetime] = {}
        # Instances whose snapshot refresh is in a transport-failure streak.
        # A failed refresh leaves the dashboard counters at their last-known
        # values (0 forever on a never-synced instance), so the first failure
        # in a streak must be visible at the default log level.  Repeats stay
        # at debug, mirroring the one ``Could not reach`` row per outage;
        # membership clears on the next successful refresh.
        self._snapshot_fail_warned: set[int] = set()
        # (instance, pass) pairs in a streak of refreshes where that pass's
        # list could not be read in full, so its cooldowns were kept.  Same
        # one-warning-per-streak rule as above, kept per pass so one list's
        # streak never hides another's first warning.
        self._incomplete_lists_warned: set[tuple[int, str]] = set()
        # Refreshes in a row whose read of a wanted list did not add up,
        # usually because the list changed mid-walk.  Such a list only joins
        # the warning above once its streak reaches _CHURN_WARN_AFTER.
        self._churn_streaks: dict[tuple[int, WantedKind], int] = {}

    # Lifecycle

    async def start(self) -> None:
        """Load enabled instances and launch one loop-task per instance."""
        instances = await list_instances(master_key=self._master_key)
        enabled = [i for i in instances if i.core.enabled]

        # Staggered by tasks started, so an instance that gets none takes no slot.
        started = 0
        for instance in enabled:
            if await self.start_instance_task(
                instance.core.id,
                instance=instance,
                startup_offset=started * _STARTUP_STAGGER_SECS,
            ):
                started += 1

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

        if not enabled:
            logger.warning("Supervisor: no enabled instances configured. Nothing to do.")
            return
        if not self._tasks:
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
            await _cancel_and_wait(snapshot)
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
        """Ensure the scheduled loop task exists for *instance_id* when enabled.

        An instance whose API key cannot be decrypted gets no task; an
        enabled one shows the error row instead, until a new key is saved.
        """
        self._prune_scheduled_tasks()

        existing = self._tasks.get(instance_id)
        if existing is not None and not existing.done():
            return False

        current = instance
        if current is None:
            current = await get_instance(instance_id, master_key=self._master_key)
        if current is None or not current.core.enabled:
            return False
        if current.core.api_key_unreadable:
            logger.info(
                "Supervisor: not starting %r (id=%d); its API key cannot be decrypted "
                "with the current master key",
                current.core.name,
                current.core.id,
            )
            await self._show_key_unreadable(current)
            return False

        # A refresh already running when the instance was stopped can record
        # state after stop_instance_task forgot it.
        self._forget_list_state(instance_id)
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
        # Written after the task exists, so a second start meanwhile sees it.
        async with self._key_row_locks.setdefault(instance_id, asyncio.Lock()):
            if _is_key_error(await self._newest_row(current)):
                await self._write_recovery_row(
                    current,
                    f"{current.core.name!r} API key can be decrypted again",
                    cycle_id=None,
                    cycle_trigger=None,
                )
        return True

    async def stop_instance_task(self, instance_id: int) -> bool:
        """Cancel scheduled and manual tasks for *instance_id*.

        Also ends its incomplete-list streaks and warnings, as starting it
        again does, so a list that stops adding up after the instance is
        re-enabled starts its count afresh and warns again.
        """
        self._prune_scheduled_tasks()
        self._prune_manual_tasks()
        self._forget_list_state(instance_id)

        stopped = False

        scheduled = self._tasks.pop(instance_id, None)
        if scheduled is not None and not scheduled.done():
            await _cancel_and_wait(scheduled)
            stopped = True

        manual = self._manual_runs.pop(instance_id, None)
        if manual is not None and not manual.done():
            await _cancel_and_wait(manual)
            stopped = True

        return stopped

    def _forget_list_state(self, instance_id: int) -> None:
        """Drop *instance_id*'s incomplete-list streaks and warnings."""
        self._churn_streaks = {k: n for k, n in self._churn_streaks.items() if k[0] != instance_id}
        self._incomplete_lists_warned = {
            k for k in self._incomplete_lists_warned if k[0] != instance_id
        }

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
        if instance.core.api_key_unreadable:
            return "key_unreadable"

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
        to become ready before the first cycle fires.  An unreachable *arr
        is retried on a short cadence (see
        :func:`houndarr.engine.retry.run_with_reconnect`); the health rows
        are written by :meth:`_run_search_cycle`.
        """
        logger.debug("Supervisor: loop started for instance id=%d", instance_id)

        logger.info(
            "Supervisor: waiting %d s startup grace for instance id=%d",
            _STARTUP_GRACE_SECS + startup_offset,
            instance_id,
        )
        await asyncio.sleep(_STARTUP_GRACE_SECS + startup_offset)

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

                if instance.core.api_key_unreadable:
                    logger.warning(
                        "Supervisor: %r API key cannot be decrypted with the current "
                        "master key; stopping loop",
                        instance.core.name,
                    )
                    await self._show_key_unreadable(instance)
                    return

                sleep_secs = await run_with_reconnect(
                    cycle=partial(self._run_search_cycle, instance, cycle_trigger="scheduled"),
                    error_retry_secs=_CONNECT_RETRY_SECS,
                    success_sleep_secs=instance.missing.sleep_interval_mins * 60,
                )
                await asyncio.sleep(sleep_secs)

        except asyncio.CancelledError:
            logger.debug("Supervisor: loop cancelled for instance id=%d", instance_id)
            raise

    async def _run_manual_once(self, instance_id: int) -> None:
        """Run one ad-hoc cycle, serialized with the scheduled loop."""
        instance = await get_instance(instance_id, master_key=self._master_key)
        if instance is None or not instance.core.enabled or instance.core.api_key_unreadable:
            return

        await self._run_search_cycle(instance, cycle_trigger="run_now")

    async def _run_search_cycle(
        self, instance: Instance, *, cycle_trigger: CycleTrigger | str
    ) -> CycleOutcome:
        """Run exactly one cycle for *instance* under the per-instance lock.

        An instance's health is its newest ``search_log`` row: the
        dashboard and the settings page both mark it failing while that
        row is an error.  This method keeps that row truthful, judging
        from the stored row rather than from memory so a restart, a
        retention purge or a manual run cannot drop or repeat a row:

        - Unreachable: one ``Could not reach`` row per outage, written
          unless the newest row already is one.
        - Failed: the cycle's error row stays newest.
        - Reached the *arr after an error: one closing row after the
          cycle's own rows, which clears the card even on an empty
          backlog where a healthy cycle writes nothing else.  When an
          outage ends on a cycle that neither ran its passes cleanly nor
          wrote rows over it, the closing row is an error that carries
          forward the error the outage covered.

        Records the cycle-end wallclock in ``_last_cycle_end`` regardless
        of cycle outcome so the dashboard countdown anchors on a signal
        that advances once per cycle even when every skip is throttled.

        Returns:
            The cycle's :class:`CycleOutcome`.
        """
        if instance.core.api_key_unreadable:
            await self._show_key_unreadable(instance)
            return CycleOutcome.failed
        lock = self._run_locks.setdefault(instance.core.id, asyncio.Lock())
        async with lock:
            cycle_id = str(uuid4())
            # Scheduled health rows carry no cycle id; a run-now row joins its
            # own cycle so the run's history shows it.
            row_cycle_id = cycle_id if cycle_trigger == "run_now" else None
            before = await self._newest_row(instance)
            report = CycleReport()
            try:
                await run_instance_search(
                    instance,
                    self._master_key,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    report=report,
                )
            except (httpx.TransportError, ClientTransportError):
                # ``ClientTransportError`` is the typed wrapper the *arr
                # clients raise instead of the raw ``httpx.TransportError``;
                # both mean the instance is unreachable.
                logger.warning(
                    "Supervisor: could not reach %r (%s); retrying in %d s",
                    instance.core.name,
                    redact_url_credentials(instance.core.url),
                    _CONNECT_RETRY_SECS,
                )
                # Re-read: the cycle may have written rows before it lost the *arr.
                if not _is_unreachable(await self._newest_row(instance)):
                    await self._write_health_row(
                        instance,
                        SearchAction.error,
                        f"{_UNREACHABLE_PREFIX}{instance.core.url}",
                        cycle_id=row_cycle_id,
                        cycle_trigger=cycle_trigger,
                    )
                return CycleOutcome.unreachable
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
                await self._write_health_row(
                    instance,
                    SearchAction.error,
                    str(exc),
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                )
                return CycleOutcome.failed
            else:
                return await self._settle_cycle(
                    instance,
                    before,
                    report,
                    cycle_id=cycle_id,
                    row_cycle_id=row_cycle_id,
                    cycle_trigger=cycle_trigger,
                )
            finally:
                self._last_cycle_end[instance.core.id] = datetime.now(UTC)

    async def _settle_cycle(
        self,
        instance: Instance,
        before: NewestLogRow | None,
        report: CycleReport,
        *,
        cycle_id: str,
        row_cycle_id: str | None,
        cycle_trigger: CycleTrigger | str,
    ) -> CycleOutcome:
        """Classify a cycle that returned, and close an earlier error it proved stale.

        A cycle can end on its own error row (an upgrade list the *arr
        refused, a failed dispatch) without raising; that leaves the
        instance failing.  Only a cycle that reached the *arr may close an
        earlier error, and only one whose passes ran cleanly may close a
        failure other than an outage: a deferred upgrade list or a
        backpressure skip proves the *arr answers, not that the failure is
        fixed.  That holds for an error an outage row covered too,
        unless the cycle wrote rows over the outage row: those are newer
        than the covered error.  A deferred cycle that nothing answered
        during an outage stays ``unreachable``.
        """
        outcome = CycleOutcome.deferred if report.upgrade_deferred else CycleOutcome.ok
        try:
            after = await fetch_newest_log_row(instance.core.id)
        except _DB_ERRORS:
            logger.warning(
                "Supervisor: could not read %r's newest log row", instance.core.name, exc_info=True
            )
            return outcome
        error = SearchAction.error.value
        if after is not None and after.cycle_id == cycle_id and after.action == error:
            return CycleOutcome.failed
        if report.upgrade_deferred and not report.reached and _is_unreachable(before):
            # Nothing answered (a read timeout), so the outage goes on: keep its quick retry.
            return CycleOutcome.unreachable
        if not report.reached or before is None or before.action != error:
            return outcome
        clean = outcome is CycleOutcome.ok and not report.passes_skipped
        if _is_unreachable(before):
            await self._close_outage(
                instance,
                None if clean else await self._row_under_outage(instance),
                cycle_id=row_cycle_id,
                cycle_trigger=cycle_trigger,
            )
        elif clean:
            await self._write_recovery_row(
                instance,
                f"{instance.core.name!r} is healthy again",
                cycle_id=row_cycle_id,
                cycle_trigger=cycle_trigger,
            )
        return outcome

    async def _close_outage(
        self,
        instance: Instance,
        unchecked: NewestLogRow | None,
        *,
        cycle_id: str | None,
        cycle_trigger: CycleTrigger | str,
    ) -> None:
        """Write the row that ends an open outage.

        *unchecked* is the instance's newest row that is not an outage row
        (pass output the closing cycle wrote, or else the row the outage
        covered), passed unless the cycle ran its passes cleanly.  If it is
        an error, the closing row stays an error and carries its message
        forward, so the instance keeps reading failing until a cycle runs
        its passes cleanly.  When it could not be read the outage stays open
        for the next cycle or refresh to close.
        """
        if unchecked is _UNREADABLE_ROW:
            return
        reachable = _reachable_again(instance)
        if unchecked is None or unchecked.action != SearchAction.error.value:
            await self._write_recovery_row(
                instance, reachable, cycle_id=cycle_id, cycle_trigger=cycle_trigger
            )
            return
        earlier = (unchecked.message or SearchAction.error.value).split(_NOT_RECHECKED, 1)[-1]
        message = f"{reachable}{_NOT_RECHECKED}{earlier}"
        logger.warning("Supervisor: %s", redact_url_credentials(message))
        await self._write_health_row(
            instance, SearchAction.error, message, cycle_id=cycle_id, cycle_trigger=cycle_trigger
        )

    async def _row_under_outage(self, instance: Instance) -> NewestLogRow | None:
        """Return the row *instance*'s open outage rows cover, or ``_UNREADABLE_ROW``."""
        try:
            return await fetch_newest_log_row_under_errors(instance.core.id, _UNREACHABLE_PREFIX)
        except _DB_ERRORS:
            logger.warning(
                "Supervisor: could not read %r's log row under its outage",
                instance.core.name,
                exc_info=True,
            )
            return _UNREADABLE_ROW

    async def _newest_row(self, instance: Instance) -> NewestLogRow | None:
        """Return *instance*'s newest log row, or ``None`` when it cannot be read."""
        try:
            return await fetch_newest_log_row(instance.core.id)
        except _DB_ERRORS:
            logger.warning(
                "Supervisor: could not read %r's newest log row", instance.core.name, exc_info=True
            )
            return None

    async def _show_key_unreadable(self, instance: Instance) -> None:
        """Write the unreadable-API-key error unless it is already the newest row.

        Re-reads the stored key under the lock the closing row is written
        under, so a key saved meanwhile is never left under this error.  An
        instance disabled meanwhile gets no row.  The row carries no trigger,
        so the Logs page shows it by default.
        """
        async with self._key_row_locks.setdefault(instance.core.id, asyncio.Lock()):
            try:
                current = await get_instance(instance.core.id, master_key=self._master_key)
            except _DB_ERRORS:
                logger.warning(
                    "Supervisor: could not re-read %r", instance.core.name, exc_info=True
                )
                return
            if current is None or not current.core.enabled or not current.core.api_key_unreadable:
                return
            if not _is_key_error(await self._newest_row(current)):
                await self._write_health_row(
                    current,
                    SearchAction.error,
                    KEY_UNREADABLE_ERROR,
                    cycle_id=None,
                    cycle_trigger=None,
                )

    async def _write_recovery_row(
        self,
        instance: Instance,
        message: str,
        *,
        cycle_id: str | None,
        cycle_trigger: CycleTrigger | str | None,
    ) -> None:
        """Log and write the ``info`` row that closes an open error."""
        logger.info("Supervisor: %s", redact_url_credentials(message))
        await self._write_health_row(
            instance, SearchAction.info, message, cycle_id=cycle_id, cycle_trigger=cycle_trigger
        )

    async def _write_health_row(
        self,
        instance: Instance,
        action: SearchAction,
        message: str,
        *,
        cycle_id: str | None,
        cycle_trigger: CycleTrigger | str | None,
    ) -> None:
        """Write a health row; a database error is logged, not raised."""
        try:
            await _write_log(
                instance_id=instance.core.id,
                item_id=None,
                item_type=None,
                action=action.value,
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                message=message,
            )
        except _DB_ERRORS:
            logger.warning(
                "Supervisor: could not write %r's health row", instance.core.name, exc_info=True
            )

    def cycle_end_timestamps(self) -> dict[int, str]:
        """Return ISO-8601 UTC timestamps for each instance's most recent cycle end.

        Populated in memory by ``_run_search_cycle`` (see its finally
        block).  Consumed by the ``/api/status`` route so the dashboard
        countdown can anchor on a signal that advances once per cycle,
        including for instances whose every skip is throttled.

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
            except Exception:
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
        transient *arr blip.  Both fetches share one walk memo, so each
        ``/wanted`` list is walked once per refresh; a pass whose list was
        not read in full, or whose reads did not add up, keeps its rows.
        """
        if not instance.core.enabled or instance.core.api_key_unreadable:
            return
        lock = self._run_locks.setdefault(instance.core.id, asyncio.Lock())
        async with lock:
            try:
                adapter: AppAdapterProto = get_adapter(instance.core.type)
                async with adapter.make_client(instance) as client:
                    walks: WantedWalks = {}
                    snap = await adapter.fetch_instance_snapshot(client, instance, walks=walks)
                    reconcile_read = False
                    try:
                        reconcile_sets = await adapter.fetch_reconcile_sets(
                            client, instance, walks=walks
                        )
                        reconcile_read = True
                    except (httpx.TransportError, ClientTransportError):
                        reconcile_sets = ReconcileSets.empty()
                        logger.debug(
                            "Supervisor: reconcile sets unreachable for %r; "
                            "keeping existing cooldowns.",
                            instance.core.name,
                        )
                    except Exception:
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
                if reconcile_read:
                    self._report_incomplete_lists(instance, reconcile_sets, walks)
                self._snapshot_fail_warned.discard(instance.core.id)
                # A successful snapshot proves the *arr is reachable, so it
                # closes an open outage row too.  That bounds how long a
                # recovered instance whose next cycle is far away stays
                # offline (Run now disabled) to the 10-minute refresh cadence.
                # A newer error from a cycle that reached the *arr stays open,
                # and so does one the outage row covered: a snapshot runs no pass.
                if _is_unreachable(await self._newest_row(instance)):
                    await self._close_outage(
                        instance,
                        await self._row_under_outage(instance),
                        cycle_id=None,
                        cycle_trigger="system",
                    )
            except (httpx.TransportError, ClientTransportError):
                if instance.core.id not in self._snapshot_fail_warned:
                    self._snapshot_fail_warned.add(instance.core.id)
                    logger.warning(
                        "Supervisor: snapshot refresh failed for %r (%s); dashboard "
                        "counts keep their last-known values until a refresh succeeds",
                        instance.core.name,
                        redact_url_credentials(instance.core.url),
                    )
                else:
                    logger.debug(
                        "Supervisor: snapshot refresh skipped for %r; instance unreachable",
                        instance.core.name,
                    )
            except Exception:
                logger.exception("Supervisor: snapshot refresh failed for %r", instance.core.name)

    def _report_incomplete_lists(
        self, instance: Instance, sets: ReconcileSets, walks: WantedWalks | None = None
    ) -> None:
        """Warn once per streak for each pass that kept its cooldowns for want of a full list.

        A wanted list whose reads did not add up (see *walks*) usually
        just changed between page reads, so it logs at debug and joins the
        warning only on its ``_CHURN_WARN_AFTER``-th such refresh in a row.
        """
        instance_id = instance.core.id
        incomplete = sets.incomplete()
        waiting: set[str] = set()
        for kind in _WANTED_KINDS:
            walk = (walks or {}).get(kind)
            if kind not in incomplete or walk is None or not walk.churned:
                self._churn_streaks.pop((instance_id, kind), None)
                continue
            streak = self._churn_streaks.get((instance_id, kind), 0) + 1
            self._churn_streaks[(instance_id, kind)] = streak
            logger.debug(
                "Supervisor: %r %s list shifted between page reads; keeping its cooldowns "
                "(%d %s in a row)",
                instance.core.name,
                kind,
                streak,
                "refresh" if streak == 1 else "refreshes",
            )
            if streak < _CHURN_WARN_AFTER:
                waiting.add(kind)
        remaining = [name for name in incomplete if name not in waiting]
        self._incomplete_lists_warned = {
            k for k in self._incomplete_lists_warned if k[0] != instance_id or k[1] in remaining
        }
        fresh = [
            name for name in remaining if (instance_id, name) not in self._incomplete_lists_warned
        ]
        repeated = [name for name in remaining if name not in fresh]
        if fresh:
            self._incomplete_lists_warned.update((instance_id, name) for name in fresh)
            logger.warning(
                "Supervisor: %r %s incomplete; keeping those cooldowns until a refresh "
                "reads them in full",
                instance.core.name,
                _name_lists(fresh),
            )
        if repeated:
            logger.debug(
                "Supervisor: %r %s still incomplete", instance.core.name, _name_lists(repeated)
            )

    async def _refresh_all_snapshots_once(self) -> None:
        """Refresh the snapshot columns for every enabled instance once.

        Runs sequentially; acquires the per-instance search lock before
        writing so it cannot race an in-progress cycle.  Disabled
        instances are skipped (their last-known snapshot stays on disk).
        So is one whose API key cannot be decrypted; an enabled one gets
        its error row back if a retention purge or Clear logs removed it.
        """
        instances = await list_instances(master_key=self._master_key)
        for inst in instances:
            if inst.core.api_key_unreadable:
                await self._show_key_unreadable(inst)
            else:
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

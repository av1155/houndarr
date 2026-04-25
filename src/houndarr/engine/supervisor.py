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
from functools import partial
from typing import Literal
from uuid import uuid4

import httpx

from houndarr.engine.search_loop import CycleTrigger, _write_log, run_instance_search
from houndarr.services.instances import Instance, get_instance, list_instances

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT = 10  # seconds to wait for tasks to finish on stop()
_CONNECT_RETRY_SECS = 30  # back-off interval when a connection error occurs
_STARTUP_GRACE_SECS = 10  # one-time delay before the first cycle fires per instance
_STARTUP_STAGGER_SECS = 30  # per-instance offset added to startup grace at initial start()
RunNowStatus = Literal["accepted", "not_found", "disabled"]


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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load enabled instances and launch one loop-task per instance."""
        instances = await list_instances(master_key=self._master_key)
        enabled = [i for i in instances if i.enabled]

        for idx, instance in enumerate(enabled):
            await self.start_instance_task(
                instance.id, instance=instance, startup_offset=idx * _STARTUP_STAGGER_SECS
            )

        if not self._tasks:
            logger.warning("Supervisor: no enabled instances configured. Nothing to do.")
            return

        await _write_log(
            instance_id=None,
            item_id=None,
            item_type=None,
            action="info",
            cycle_trigger="system",
            message=f"Supervisor started {len(self._tasks)} task(s)",
        )

    async def stop(self) -> None:
        """Cancel all running tasks and wait up to 10 s for clean exit."""
        self._prune_scheduled_tasks()
        self._prune_manual_tasks()

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
        if current is None or not current.enabled:
            return False

        task = asyncio.create_task(
            self._instance_loop(instance_id, startup_offset=startup_offset),
            name=f"search-loop-{instance_id}",
        )
        task.add_done_callback(partial(self._on_scheduled_task_done, instance_id))
        self._tasks[instance_id] = task
        logger.info("Supervisor: started task for instance %r (id=%d)", current.name, current.id)
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
        if instance is None or not instance.enabled:
            await self.stop_instance_task(instance_id)
            return

        await self.start_instance_task(instance_id, instance=instance)

    async def trigger_run_now(self, instance_id: int) -> RunNowStatus:
        """Queue one immediate search cycle for *instance_id* if active."""
        self._prune_manual_tasks()

        instance = await get_instance(instance_id, master_key=self._master_key)
        if instance is None:
            return "not_found"
        if not instance.enabled:
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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _instance_loop(self, instance_id: int, startup_offset: int = 0) -> None:
        """Run search cycles for one instance until cancelled.

        A one-time startup grace delay gives co-located *arr services time
        to become ready before the first cycle fires.  Connection errors are
        logged to ``search_log`` only on state transitions (first failure and
        recovery) to avoid inflating the dashboard error counter with retry noise.
        """
        logger.debug("Supervisor: loop started for instance id=%d", instance_id)
        _in_connect_retry = False

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

                if not instance.enabled:
                    logger.info(
                        "Supervisor: instance %r disabled; stopping loop",
                        instance.name,
                    )
                    return

                got_connect_error = await self._run_search_cycle(
                    instance, cycle_trigger="scheduled"
                )

                if got_connect_error:
                    if not _in_connect_retry:
                        # First failure: write one error row and enter retry state.
                        await _write_log(
                            instance_id=instance.id,
                            item_id=None,
                            item_type=None,
                            action="error",
                            cycle_trigger="scheduled",
                            message=f"Could not reach {instance.url}",
                        )
                    _in_connect_retry = True
                    await asyncio.sleep(_CONNECT_RETRY_SECS)
                else:
                    if _in_connect_retry:
                        # Recovery: write one info row and leave retry state.
                        logger.info(
                            "Supervisor: %r (%s) is reachable again",
                            instance.name,
                            instance.url,
                        )
                        await _write_log(
                            instance_id=instance.id,
                            item_id=None,
                            item_type=None,
                            action="info",
                            cycle_trigger="scheduled",
                            message=f"{instance.name!r} ({instance.url}) is reachable again",
                        )
                        _in_connect_retry = False
                    await asyncio.sleep(instance.sleep_interval_mins * 60)

        except asyncio.CancelledError:
            logger.debug("Supervisor: loop cancelled for instance id=%d", instance_id)
            raise

    async def _run_manual_once(self, instance_id: int) -> None:
        """Run one ad-hoc cycle, serialized with the scheduled loop."""
        instance = await get_instance(instance_id, master_key=self._master_key)
        if instance is None or not instance.enabled:
            return

        await self._run_search_cycle(instance, cycle_trigger="run_now")

    async def _run_search_cycle(self, instance: Instance, *, cycle_trigger: CycleTrigger) -> bool:
        """Run exactly one cycle for *instance* under the per-instance lock.

        Returns:
            ``True`` if the cycle failed with a connection error, ``False`` otherwise.
        """
        lock = self._run_locks.setdefault(instance.id, asyncio.Lock())
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
                    instance.name,
                    instance.url,
                    _CONNECT_RETRY_SECS,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Supervisor: unhandled error in search loop for %r: %s",
                    instance.name,
                    exc,
                )
                await _write_log(
                    instance_id=instance.id,
                    item_id=None,
                    item_type=None,
                    action="error",
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    message=str(exc),
                )
                return False

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

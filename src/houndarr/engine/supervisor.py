"""Background supervisor — manages one asyncio.Task per enabled instance.

The supervisor is started once during application lifespan and runs until
shutdown.  Each task loops indefinitely: run a search cycle, sleep for
``sleep_interval_mins``, repeat.  Cancellation (on shutdown) is handled
gracefully.
"""

from __future__ import annotations

import asyncio
import logging

from houndarr.engine.search_loop import _write_log, run_instance_search
from houndarr.services.instances import list_instances

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT = 10  # seconds to wait for tasks to finish on stop()


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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load enabled instances and launch one loop-task per instance."""
        instances = await list_instances(master_key=self._master_key)
        enabled = [i for i in instances if i.enabled]

        if not enabled:
            logger.warning("Supervisor: no enabled instances configured — nothing to do")
            return

        for instance in enabled:
            task = asyncio.create_task(
                self._instance_loop(instance.id),
                name=f"search-loop-{instance.id}",
            )
            self._tasks[instance.id] = task
            logger.info(
                "Supervisor: started task for instance %r (id=%d)", instance.name, instance.id
            )

        await _write_log(
            instance_id=None,
            item_id=None,
            item_type=None,
            action="info",
            message=f"Supervisor started {len(self._tasks)} task(s)",
        )

    async def stop(self) -> None:
        """Cancel all running tasks and wait up to 10 s for clean exit."""
        if not self._tasks:
            return

        for task in self._tasks.values():
            task.cancel()

        done, pending = await asyncio.wait(
            list(self._tasks.values()),
            timeout=_SHUTDOWN_TIMEOUT,
        )

        # Force-cancel anything that outlived the timeout
        for task in pending:
            task.cancel()
            logger.warning("Supervisor: task did not finish within timeout — force cancelled")

        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error("Supervisor: task raised unexpected exception: %s", exc)

        self._tasks.clear()
        logger.info("Supervisor: all tasks stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _instance_loop(self, instance_id: int) -> None:
        """Run search cycles for one instance until cancelled."""
        logger.debug("Supervisor: loop started for instance id=%d", instance_id)
        try:
            while True:
                # Re-fetch the instance on each cycle so config changes take effect
                from houndarr.services.instances import get_instance

                instance = await get_instance(instance_id, master_key=self._master_key)
                if instance is None:
                    logger.warning(
                        "Supervisor: instance id=%d no longer exists — stopping loop",
                        instance_id,
                    )
                    return

                if not instance.enabled:
                    logger.info(
                        "Supervisor: instance %r disabled — sleeping until re-enabled",
                        instance.name,
                    )
                else:
                    try:
                        await run_instance_search(instance, self._master_key)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "Supervisor: unhandled error in search loop for %r: %s",
                            instance.name,
                            exc,
                        )
                        await _write_log(
                            instance_id=instance_id,
                            item_id=None,
                            item_type=None,
                            action="error",
                            message=str(exc),
                        )

                await asyncio.sleep(instance.sleep_interval_mins * 60)

        except asyncio.CancelledError:
            logger.debug("Supervisor: loop cancelled for instance id=%d", instance_id)
            raise

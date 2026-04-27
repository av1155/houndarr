"""Reconnect-state machine for the supervisor's per-instance loop.

The supervisor's per-cycle outcome is governed by a small state machine:
write a single ``error`` row on the first failed cycle in a streak,
suppress further error rows until the streak ends, then write a
single ``info`` recovery row on the first successful cycle that
follows.  This file owns that logic so the supervisor's ``while True``
loop body stays straight-line and the state-transition rules can be
exercised directly.

The helper is deliberately hand-rolled (no ``tenacity`` /
``httpx-retries`` dependency) because the state surface is small:
a :class:`ReconnectState` plus one :func:`run_with_reconnect`
driver are enough for every retry path in the codebase.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from houndarr.enums import CycleTrigger, SearchAction
from houndarr.services.instances import Instance

logger = logging.getLogger(__name__)


def _apply_jitter(secs: int, jitter_secs: float | None) -> float:
    """Return ``secs`` with up to ``jitter_secs`` seconds of symmetric jitter.

    Uses :mod:`secrets` so there is no seeded reproducibility footgun;
    tests that need determinism pass ``jitter_secs=None`` (the
    default) to keep the returned value exactly equal to ``secs``.
    Negative results are clamped to ``0.0`` so a large jitter band
    never pushes the sleep below zero.
    """
    if jitter_secs is None or jitter_secs <= 0.0:
        return float(secs)
    # ``secrets.SystemRandom().uniform`` is the documented unseeded
    # spelling; ``secrets.randbelow`` is integer-only.
    delta = secrets.SystemRandom().uniform(-jitter_secs, jitter_secs)
    return max(0.0, float(secs) + delta)


@dataclass
class ReconnectState:
    """Per-instance state carried across supervisor loop iterations.

    ``in_retry`` is true between the first failed cycle in a streak
    and the first successful cycle that follows.  Mutated by
    :func:`run_with_reconnect`.

    The dataclass is deliberately not frozen and not slotted: the
    helper mutates ``in_retry`` in place across many awaits and a
    frozen instance would require returning a new object every call.
    """

    in_retry: bool = False


async def run_with_reconnect(
    state: ReconnectState,
    *,
    instance: Instance,
    cycle: Callable[[], Awaitable[bool]],
    cycle_trigger: CycleTrigger | str,
    error_retry_secs: int,
    success_sleep_secs: int,
    write_log: Callable[..., Awaitable[None]],
    jitter_secs: float | None = None,
) -> float:
    """Run *cycle* and apply per-instance reconnect-state transitions.

    *cycle* is a zero-arg awaitable that runs one search cycle and
    returns ``True`` iff the cycle failed with a connection error,
    ``False`` otherwise.  This helper translates that outcome into
    state-transition log rows and returns how long the caller should
    sleep before invoking the next cycle.

    State transitions:

    - First failure in a streak: writes one ``error`` row with the
        message ``Could not reach <url>``, sets ``state.in_retry``,
        returns ``error_retry_secs``.
    - Subsequent failures in the same streak: returns
        ``error_retry_secs`` without a log write (the dashboard error
        counter must not inflate with retry noise).
    - First success after a streak: writes one ``info`` recovery row
        with the message ``'<name>' (<url>) is reachable again``,
        clears ``state.in_retry``, returns ``success_sleep_secs``.
    - Steady-state success: returns ``success_sleep_secs`` without a
        log write.

    Args:
        state: The per-instance reconnect state object the helper
            mutates.  The caller is expected to keep one
            :class:`ReconnectState` per instance loop and pass the
            same instance every iteration.
        instance: The current Instance snapshot (refreshed by the
            caller each iteration so live setting changes apply).
        cycle: A zero-arg awaitable that runs one search cycle and
            returns ``True`` iff the cycle failed with a connection
            error.
        cycle_trigger: The trigger string written to ``search_log``
            on state transitions.
        error_retry_secs: Sleep duration after a failure (whether or
            not a row is written this iteration).
        success_sleep_secs: Sleep duration after a success.
        write_log: The ``search_log`` writer callback.  Threading the
            writer in keeps this module free of any direct dependency
            on ``engine.search_loop`` and lets tests substitute a
            spy.
        jitter_secs: Optional symmetric jitter window in seconds
            applied to the returned sleep duration (uniform random
            in ``[-jitter_secs, +jitter_secs]``, clamped to ``>= 0``).
            ``None`` (the default) keeps the sleep exactly equal to
            the nominal value so the supervisor's pre-jitter
            behaviour is preserved.  Opt-in jitter de-synchronises
            re-ping storms when several instances come back online
            at the same moment.

    Returns:
        Number of seconds to sleep before the next cycle.  ``float``
        even when ``jitter_secs`` is ``None`` so the return shape is
        stable across both call modes.
    """
    got_connect_error = await cycle()

    if got_connect_error:
        if not state.in_retry:
            await write_log(
                instance_id=instance.core.id,
                item_id=None,
                item_type=None,
                action=SearchAction.error.value,
                cycle_trigger=cycle_trigger,
                message=f"Could not reach {instance.core.url}",
            )
        state.in_retry = True
        return _apply_jitter(error_retry_secs, jitter_secs)

    if state.in_retry:
        logger.info(
            "Reconnect: %r (%s) is reachable again",
            instance.core.name,
            instance.core.url,
        )
        await write_log(
            instance_id=instance.core.id,
            item_id=None,
            item_type=None,
            action=SearchAction.info.value,
            cycle_trigger=cycle_trigger,
            message=f"{instance.core.name!r} ({instance.core.url}) is reachable again",
        )
        state.in_retry = False
    return _apply_jitter(success_sleep_secs, jitter_secs)

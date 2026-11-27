"""Sleep scheduling for the supervisor's per-instance loop.

An unreachable *arr is retried on a short cadence so recovery is noticed
quickly; every other cycle outcome waits the instance's normal interval.
The ``search_log`` rows that mark an instance failing or recovered are
written by ``Supervisor._run_search_cycle``.

The helper is deliberately hand-rolled (no ``tenacity`` /
``httpx-retries`` dependency) because the surface is one function.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from enum import StrEnum


class CycleOutcome(StrEnum):
    """How one search cycle ended.

    - ``ok``: the cycle finished and its own rows hold no error.
    - ``unreachable``: the *arr could not be reached.
    - ``failed``: the *arr answered but the cycle ended on an error row.
    - ``deferred``: the upgrade list could not be read for a reason
      that is expected to pass (5xx, read timeout, wire validation), so
      the cycle neither marks the instance failing nor clears it.
    """

    ok = "ok"
    unreachable = "unreachable"
    failed = "failed"
    deferred = "deferred"


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


async def run_with_reconnect(
    *,
    cycle: Callable[[], Awaitable[CycleOutcome]],
    error_retry_secs: int,
    success_sleep_secs: int,
    jitter_secs: float | None = None,
) -> float:
    """Run *cycle* and return how long to sleep before the next one.

    Args:
        cycle: A zero-arg awaitable that runs one search cycle and
            reports its :class:`CycleOutcome`.
        error_retry_secs: Sleep after an ``unreachable`` cycle.
        success_sleep_secs: Sleep after any other outcome.  A cycle
            that reached the *arr and failed waits the normal interval:
            its error row already marks the instance failing, and
            retrying a rejecting *arr every few seconds would only
            repeat the rejection.
        jitter_secs: Optional symmetric jitter window in seconds
            applied to the returned sleep duration (uniform random
            in ``[-jitter_secs, +jitter_secs]``, clamped to ``>= 0``).
            ``None`` (the default) keeps the sleep exactly equal to
            the nominal value.  Opt-in jitter de-synchronises re-ping
            storms when several instances come back online at the
            same moment.

    Returns:
        Number of seconds to sleep before the next cycle.  ``float``
        even when ``jitter_secs`` is ``None`` so the return shape is
        stable across both call modes.
    """
    outcome = await cycle()
    secs = error_retry_secs if outcome is CycleOutcome.unreachable else success_sleep_secs
    return _apply_jitter(secs, jitter_secs)

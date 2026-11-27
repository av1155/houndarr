"""Characterisation pins for run_with_reconnect timing semantics.

The deterministic default-path contract and the opt-in
``jitter_secs`` kwarg behaviour are both pinned here.  Keeping the
deterministic assertion in the pinning suite guards against a
future kwarg default drift that would silently introduce randomness
on the default path.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

import pytest

from houndarr.engine.retry import CycleOutcome, run_with_reconnect

pytestmark = pytest.mark.pinning


def _cycle(outcome: CycleOutcome) -> Callable[[], Awaitable[CycleOutcome]]:
    async def cycle() -> CycleOutcome:
        return outcome

    return cycle


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "outcome",
    [o for o in CycleOutcome if o is not CycleOutcome.unreachable],
)
async def test_reconnect_default_path_is_deterministic_after_contact(
    outcome: CycleOutcome,
) -> None:
    """Every outcome but ``unreachable`` returns exactly ``success_sleep_secs``.

    A cycle that reached the *arr and failed is not retried on the
    quick connect-retry cadence: its error row already marks the
    instance failing, and hammering a rejecting *arr every 30 s would
    only repeat it.
    """
    delay = await run_with_reconnect(
        cycle=_cycle(outcome),
        error_retry_secs=30,
        success_sleep_secs=1800,
    )

    assert delay == 1800


@pytest.mark.asyncio()
async def test_reconnect_default_path_is_deterministic_when_unreachable() -> None:
    """An unreachable cycle must return exactly ``error_retry_secs``."""
    delay = await run_with_reconnect(
        cycle=_cycle(CycleOutcome.unreachable),
        error_retry_secs=30,
        success_sleep_secs=1800,
    )

    assert delay == 30


def test_run_with_reconnect_signature_includes_jitter_secs() -> None:
    """Pin the signature shape of run_with_reconnect.

    ``jitter_secs`` must be keyword-only with a ``None`` default so
    the supervisor's zero-jitter call site stays byte-stable.  A
    future rename or default flip trips this pin.
    """
    signature = inspect.signature(run_with_reconnect)
    keyword_only = {
        name for name, p in signature.parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert keyword_only == set(signature.parameters)
    assert keyword_only == {"cycle", "error_retry_secs", "success_sleep_secs", "jitter_secs"}

    jitter_param = signature.parameters["jitter_secs"]
    assert jitter_param.default is None


@pytest.mark.asyncio()
async def test_reconnect_jitter_respects_bound() -> None:
    """With ``jitter_secs`` set the return value stays inside ``[secs - j, secs + j]``.

    We sample enough iterations that a symmetric ``uniform(-j, j)``
    covers a spread wider than the ``nominal == returned`` case a
    missing jitter path would yield.  Asserting the bound rather
    than a specific value keeps the pin deterministic without
    reaching into the SystemRandom state.
    """
    observed: list[float] = []
    for _ in range(128):
        delay = await run_with_reconnect(
            cycle=_cycle(CycleOutcome.ok),
            error_retry_secs=30,
            success_sleep_secs=1800,
            jitter_secs=10.0,
        )
        observed.append(delay)

    assert all(1790.0 <= d <= 1810.0 for d in observed), sorted(observed)[:5]
    # At least one sample must drift from the nominal value so the
    # default-path determinism pin above cannot accidentally pass
    # under a jittered call.
    assert any(d != 1800.0 for d in observed)


@pytest.mark.asyncio()
async def test_reconnect_jitter_clamps_to_non_negative() -> None:
    """``jitter_secs`` larger than the nominal sleep must not push the
    returned duration below zero.

    ``_apply_jitter`` clamps via ``max(0.0, secs + delta)``.  With
    ``error_retry_secs=5`` and ``jitter_secs=60``, ``uniform(-60, +60)``
    can land as low as ``-55`` pre-clamp, which would be a
    nonsensical ``asyncio.sleep`` argument.  The clamp must catch it.
    128 iterations give ~99.99% coverage of the negative-draw branch.
    """
    observed: list[float] = []
    for _ in range(128):
        delay = await run_with_reconnect(
            cycle=_cycle(CycleOutcome.unreachable),
            error_retry_secs=5,
            success_sleep_secs=1800,
            jitter_secs=60.0,
        )
        observed.append(delay)

    assert all(d >= 0.0 for d in observed), sorted(observed)[:5]
    # Upper bound stays finite: nominal + jitter_secs.
    assert all(d <= 65.0 for d in observed), sorted(observed, reverse=True)[:5]
    # The clamp MUST fire on at least one sample (otherwise the test
    # is not exercising the branch it claims to pin).
    assert any(d == 0.0 for d in observed), (
        "jitter_secs=60 against error_retry_secs=5 should produce "
        "clamped-to-zero samples in ~50% of draws; none observed"
    )

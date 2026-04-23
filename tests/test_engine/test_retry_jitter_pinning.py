"""Characterisation pins for run_with_reconnect timing semantics.

Phase 1 locks the deterministic pre-jitter contract; Phase 5b appends a
second pin (``test_reconnect_jitter_respects_bound``) that exercises the
new ``jitter_secs`` kwarg once it lands.  Keeping the deterministic
assertion in the pinning suite guards against a future kwarg default
drift that would silently introduce randomness on the default path.
"""

from __future__ import annotations

import inspect

import pytest

from houndarr.engine.retry import ReconnectState, run_with_reconnect
from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    MissingPolicy,
    RuntimeSnapshot,
    SchedulePolicy,
    UpgradePolicy,
)

pytestmark = pytest.mark.pinning


def _make_instance() -> Instance:
    return Instance(
        core=InstanceCore(
            id=42,
            name="Retry Pin",
            type=InstanceType.sonarr,
            url="http://sonarr:8989",
            api_key="ignored",
        ),
        missing=MissingPolicy(),
        cutoff=CutoffPolicy(),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
    )


@pytest.mark.asyncio()
async def test_reconnect_default_path_is_deterministic_on_success() -> None:
    """A successful cycle must return exactly ``success_sleep_secs``.

    Locks the pre-jitter contract: zero randomness on the success
    branch.  Phase 5b will add an opt-in ``jitter_secs`` kwarg; the
    default path must stay deterministic after the kwarg lands.
    """
    state = ReconnectState()
    log_calls: list[dict[str, object]] = []

    async def log(**kwargs: object) -> None:
        log_calls.append(kwargs)

    async def cycle_ok() -> bool:
        return False  # success

    delay = await run_with_reconnect(
        state,
        instance=_make_instance(),
        cycle=cycle_ok,
        cycle_trigger="scheduled",
        error_retry_secs=30,
        success_sleep_secs=1800,
        write_log=log,
    )

    assert delay == 1800
    assert log_calls == []
    assert state.in_retry is False


@pytest.mark.asyncio()
async def test_reconnect_default_path_is_deterministic_on_error() -> None:
    """A failed cycle must return exactly ``error_retry_secs``."""
    state = ReconnectState()
    log_calls: list[dict[str, object]] = []

    async def log(**kwargs: object) -> None:
        log_calls.append(kwargs)

    async def cycle_fail() -> bool:
        return True  # connect error

    delay = await run_with_reconnect(
        state,
        instance=_make_instance(),
        cycle=cycle_fail,
        cycle_trigger="scheduled",
        error_retry_secs=30,
        success_sleep_secs=1800,
        write_log=log,
    )

    assert delay == 30
    assert state.in_retry is True
    # First error in a streak writes exactly one error row.
    assert len(log_calls) == 1
    assert log_calls[0]["action"] == "error"


def test_run_with_reconnect_signature_includes_jitter_secs() -> None:
    """Pin the signature shape after Phase 5b added the jitter_secs kwarg.

    ``jitter_secs`` must be keyword-only with a ``None`` default so
    the supervisor's zero-jitter call site stays byte-stable.  A
    future rename or default flip trips this pin.
    """
    signature = inspect.signature(run_with_reconnect)
    names = list(signature.parameters)
    assert names[0] == "state"
    keyword_only = {
        name for name, p in signature.parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    expected_keyword_only = {
        "instance",
        "cycle",
        "cycle_trigger",
        "error_retry_secs",
        "success_sleep_secs",
        "write_log",
        "jitter_secs",
    }
    assert keyword_only == expected_keyword_only

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

    async def cycle_ok() -> bool:
        return False

    async def log(**_: object) -> None:
        return None

    observed: list[float] = []
    for _ in range(128):
        state = ReconnectState()
        delay = await run_with_reconnect(
            state,
            instance=_make_instance(),
            cycle=cycle_ok,
            cycle_trigger="scheduled",
            error_retry_secs=30,
            success_sleep_secs=1800,
            write_log=log,
            jitter_secs=10.0,
        )
        observed.append(delay)

    assert all(1790.0 <= d <= 1810.0 for d in observed), sorted(observed)[:5]
    # At least one sample must drift from the nominal value so the
    # default-path determinism pin above cannot accidentally pass
    # under a jittered call.
    assert any(d != 1800.0 for d in observed)

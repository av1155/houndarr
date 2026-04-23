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


def test_run_with_reconnect_signature_matches_pre_jitter_shape() -> None:
    """Pin the signature so Phase 5b's kwarg addition is the only diff.

    Phase 5b must add ``jitter_secs: float | None = None`` as a
    keyword-only parameter.  If the signature drifts in any other way
    (rename, default change, positional promotion) this pin fails and
    the cause is made visible before Phase 5b picks up the diff.
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
    }
    # Phase 1 pin: keyword-only set matches pre-jitter shape exactly.
    # Phase 5b updates this test to expand the set with ``jitter_secs``.
    assert keyword_only == expected_keyword_only

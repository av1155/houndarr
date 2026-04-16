"""Tests for the allowed-time-window gate in run_instance_search()."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from houndarr.engine import search_loop
from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import InstanceType

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    MASTER_KEY,
    SONARR_URL,
    get_log_rows,
    make_instance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MISSING_SONARR_ONE: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_EPISODE_RECORD],
}


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 10,
        "hourly_cap": 20,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _mock_missing_and_command() -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR_ONE),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


def _freeze_now(monkeypatch: pytest.MonkeyPatch, at_utc_hour: int, at_utc_minute: int = 0) -> None:
    """Patch ``search_loop.datetime.now`` to return a fixed UTC moment.

    The gate calls ``datetime.now(UTC).astimezone().time()`` and tests need
    a deterministic "now" regardless of where they run.  Using UTC as the
    fixed timezone removes any dependency on the test host's local zone:
    ``.astimezone()`` with no arg converts to local, but our inputs are
    already UTC-based and the gate only looks at hour/minute, so pinning
    the local TZ to UTC via the monkeypatch keeps the comparison direct.
    """
    fixed = datetime(2026, 4, 16, at_utc_hour, at_utc_minute, tzinfo=UTC)

    class _PinnedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            if tz is None:
                return fixed.replace(tzinfo=None)
            return fixed.astimezone(tz)

    # Ensure the test treats "local" as UTC so astimezone() is a no-op;
    # this lets us reason about the configured window in UTC hours.
    monkeypatch.setenv("TZ", "UTC")
    # time.tzset is POSIX-only but always available on Linux/macOS CI.
    import time

    if hasattr(time, "tzset"):
        time.tzset()

    monkeypatch.setattr(search_loop, "datetime", _PinnedDatetime)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_empty_window_allows_cycle(seeded_instances: None) -> None:
    """Empty allowed_time_window means no gate; cycle runs normally."""
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="")
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_inside_window_allows_cycle(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Time inside the configured window lets the cycle proceed."""
    _freeze_now(monkeypatch, at_utc_hour=10)
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="09:00-12:00")
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_outside_window_skips_cycle_with_info_row(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside the window, the cycle returns 0 and logs an info row."""
    _freeze_now(monkeypatch, at_utc_hour=14)
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="09:00-12:00")
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "info"
    assert rows[0]["reason"] == "outside allowed time window"
    assert "14:00" in rows[0]["message"]
    assert "09:00-12:00" in rows[0]["message"]


@pytest.mark.asyncio()
@respx.mock
async def test_outside_window_does_not_call_arr(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate must short-circuit before any HTTP call is made."""
    _freeze_now(monkeypatch, at_utc_hour=14)

    # Do NOT mock anything: if the gate is bypassed, the respx strict
    # matcher will raise because no route matches, which surfaces the bug.
    inst = _sonarr_instance(allowed_time_window="09:00-12:00")
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


@pytest.mark.asyncio()
@respx.mock
async def test_multiple_windows_match_any(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If any configured range contains now, the cycle runs."""
    _freeze_now(monkeypatch, at_utc_hour=15)
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="09:00-12:00,14:00-18:00")
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_wraparound_window_allows_late_evening(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """22:00-06:00 matches 23:00."""
    _freeze_now(monkeypatch, at_utc_hour=23)
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="22:00-06:00")
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_bypasses_gate(
    seeded_instances: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual Run Now triggers bypass the time window deliberately."""
    _freeze_now(monkeypatch, at_utc_hour=14)
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="09:00-12:00")
    count = await run_instance_search(inst, MASTER_KEY, cycle_trigger="run_now")

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_malformed_window_fails_open_and_logs_warning(
    seeded_instances: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad spec (shouldn't happen via UI) fails open with a warning log."""
    _mock_missing_and_command()

    inst = _sonarr_instance(allowed_time_window="not a real spec")
    with caplog.at_level("WARNING"):
        count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    assert any("malformed allowed_time_window" in rec.message for rec in caplog.records)

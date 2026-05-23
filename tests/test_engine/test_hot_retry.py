"""Tests for missing-pass hot retry windows after post-release grace."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from houndarr.database import get_db
from houndarr.engine import search_loop
from houndarr.engine.search_loop import run_instance_search
from houndarr.enums import CycleTrigger
from houndarr.repositories import cooldowns as cooldowns_repo
from houndarr.repositories import search_log as search_log_repo
from houndarr.services.instances import InstanceType, SearchOrder

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    MASTER_KEY,
    SONARR_URL,
    get_log_rows,
    make_instance,
)

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _page(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "page": 1,
        "pageSize": 50,
        "totalRecords": len(records),
        "records": records,
    }


def _released_episode() -> dict[str, Any]:
    return {**_EPISODE_RECORD, "airDateUtc": "2026-05-21T00:00:00Z"}


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 14,
        "post_release_grace_hrs": 0,
        "missing_hot_retry_window_hrs": 24,
        "missing_hot_retry_interval_hrs": 2,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _freeze_now(monkeypatch: pytest.MonkeyPatch, now: datetime = _NOW) -> None:
    class _PinnedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    monkeypatch.setattr(search_loop, "datetime", _PinnedDatetime)
    monkeypatch.setattr(search_log_repo, "datetime", _PinnedDatetime)
    monkeypatch.setattr(cooldowns_repo, "_now_utc", lambda: now)


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def _seed_hot_retry_state(
    *,
    grace_age: timedelta,
    cooldown_age: timedelta,
    include_shadow_search: bool = True,
) -> None:
    grace_at = _NOW - grace_age
    cooldown_at = _NOW - cooldown_age
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, 101, "episode", "missing", _iso(cooldown_at)),
        )
        await conn.execute(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, search_kind, action, reason, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                101,
                "episode",
                "missing",
                "skipped",
                "post-release grace (6h)",
                _iso(grace_at),
            ),
        )
        if include_shadow_search:
            await conn.execute(
                """
                INSERT INTO search_log (
                    instance_id, item_id, item_type, search_kind, action, reason, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (1, 101, "episode", "missing", "searched", None, _iso(cooldown_at)),
            )
        await conn.commit()


async def _seed_cooldown_only(*, cooldown_age: timedelta) -> None:
    cooldown_at = _NOW - cooldown_age
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, 101, "episode", "missing", _iso(cooldown_at)),
        )
        await conn.execute(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, search_kind, action, reason, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 101, "episode", "missing", "searched", None, _iso(cooldown_at)),
        )
        await conn.commit()


def _mock_missing() -> None:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_released_episode()])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


@pytest.mark.asyncio()
@respx.mock
async def test_window_zero_preserves_one_shot_shadow_behavior(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """window=0 does not refire after a searched row shadows the grace skip."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=1))
    _mock_missing()

    count = await run_instance_search(
        _sonarr_instance(missing_hot_retry_window_hrs=0),
        MASTER_KEY,
    )

    assert count == 0
    rows = await get_log_rows()
    assert not any(r["reason"] == "in hot retry window (24h)" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_enabled_window_preserves_not_yet_released_one_shot(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hot retry window does not remove the legacy not-yet-released retry."""
    _freeze_now(monkeypatch)
    await _seed_cooldown_only(cooldown_age=timedelta(hours=3))
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, search_kind, action, reason, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                101,
                "episode",
                "missing",
                "skipped",
                "not yet released",
                _iso(_NOW - timedelta(hours=2)),
            ),
        )
        await conn.commit()
    _mock_missing()
    inst = _sonarr_instance()

    assert await run_instance_search(inst, MASTER_KEY) == 1
    assert await run_instance_search(inst, MASTER_KEY) == 0


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retry_fires_inside_window_after_interval(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grace skip inside the window can retry after the interval elapses."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=3))
    _mock_missing()

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["item_id"] == 101]
    assert len(searched) == 2


@pytest.mark.asyncio()
@respx.mock
async def test_multiple_hot_retries_respect_interval(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot retries can repeat inside the window only after the interval."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=3))
    _mock_missing()
    inst = _sonarr_instance()

    assert await run_instance_search(inst, MASTER_KEY) == 1
    assert await run_instance_search(inst, MASTER_KEY) == 0

    _freeze_now(monkeypatch, _NOW + timedelta(hours=2))
    assert await run_instance_search(inst, MASTER_KEY) == 1

    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["item_id"] == 101]
    assert len(searched) == 3


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retry_throttles_inside_window_before_interval(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grace skip inside the window waits until the interval elapses."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=1))
    _mock_missing()

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    assert any(r["reason"] == "in hot retry window (24h)" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retry_window_expiry_falls_through_to_cooldown(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired grace anchor uses the normal missing cooldown branch."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=24), cooldown_age=timedelta(hours=3))
    _mock_missing()

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    assert any(r["reason"] == "on cooldown (14d)" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retry_requires_grace_anchor(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled hot retry falls through to cooldown without a grace skip anchor."""
    _freeze_now(monkeypatch)
    await _seed_cooldown_only(cooldown_age=timedelta(hours=3))
    _mock_missing()

    count = await run_instance_search(_sonarr_instance(), MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    assert any(r["reason"] == "on cooldown (14d)" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_respects_expired_hot_retry_window(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual Run Now does not reopen an expired hot retry window."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=24), cooldown_age=timedelta(hours=1))
    _mock_missing()

    count = await run_instance_search(
        _sonarr_instance(),
        MASTER_KEY,
        cycle_trigger=CycleTrigger.run_now,
    )

    assert count == 0
    rows = await get_log_rows()
    assert any(r["reason"] == "on cooldown (14d)" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_hourly_cap_throttles_hot_retry(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hourly cap runs before hot retry dispatch."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(
        grace_age=timedelta(hours=6),
        cooldown_age=timedelta(minutes=30),
    )
    _mock_missing()

    count = await run_instance_search(_sonarr_instance(hourly_cap=1), MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    assert any(r["reason"] == "hourly limit reached (1/hr)" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retry_skip_is_rate_limited(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated scheduled interval skips write at most one hot-retry row."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=1))
    _mock_missing()
    inst = _sonarr_instance()

    assert await run_instance_search(inst, MASTER_KEY) == 0
    assert await run_instance_search(inst, MASTER_KEY) == 0

    rows = await get_log_rows()
    hot_retry_rows = [r for r in rows if r["reason"] == "in hot retry window (24h)"]
    assert len(hot_retry_rows) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_bypasses_hot_retry_interval(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual Run Now bypasses interval but still stays inside the window."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=1))
    _mock_missing()

    count = await run_instance_search(
        _sonarr_instance(),
        MASTER_KEY,
        cycle_trigger=CycleTrigger.run_now,
    )

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_allowed_time_window_blocks_scheduled_hot_retry(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator time window gates hot retry before missing items are fetched."""
    _freeze_now(monkeypatch)
    await _seed_hot_retry_state(grace_age=timedelta(hours=6), cooldown_age=timedelta(hours=3))
    _mock_missing()
    inst = _sonarr_instance(
        allowed_time_window="13:00-14:00",
        search_order=SearchOrder.chronological,
    )

    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    assert respx.calls.call_count == 0

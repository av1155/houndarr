"""Tests for release-timing retry and grace window behavior."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from houndarr.engine.search_loop import (
    _is_release_timing_reason,
    run_instance_search,
)
from houndarr.services.cooldown import should_log_skip
from houndarr.services.instances import InstanceType

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    _FUTURE_AIR_DATE,
    MASTER_KEY,
    SONARR_URL,
    get_log_rows,
    make_instance,
    seed_release_timing_retry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_PAGE: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 0,
    "records": [],
}


def _page(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "page": 1,
        "pageSize": 50,
        "totalRecords": len(records),
        "records": records,
    }


def _sonarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _radarr_instance(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 2,
        "itype": InstanceType.radarr,
        "batch_size": 10,
        "hourly_cap": 0,
        "cooldown_days": 7,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


# ---------------------------------------------------------------------------
# Unit tests for _is_release_timing_reason
# ---------------------------------------------------------------------------


def test_is_release_timing_reason_true_not_yet_released() -> None:
    """'not yet released' is a release-timing reason."""
    assert _is_release_timing_reason("not yet released") is True


def test_is_release_timing_reason_true_grace() -> None:
    """'post-release grace (6h)' is a release-timing reason."""
    assert _is_release_timing_reason("post-release grace (6h)") is True


def test_is_release_timing_reason_false_cooldown() -> None:
    """'on cooldown (14d)' is NOT a release-timing reason."""
    assert _is_release_timing_reason("on cooldown (14d)") is False


def test_is_release_timing_reason_false_none() -> None:
    """None is NOT a release-timing reason."""
    assert _is_release_timing_reason(None) is False


# ---------------------------------------------------------------------------
# Release-timing retry in missing pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_missing_pass(
    seeded_instances: None,
) -> None:
    """Item on cooldown with 'not yet released' reason: retried in missing pass."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="not yet released",
    )

    # Episode now has a past air date (released)
    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_cutoff_blocked(
    seeded_instances: None,
) -> None:
    """Same item in cutoff pass: stays on cooldown (no retry path)."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="not yet released",
    )

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_EMPTY_PAGE),
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(
        cutoff_enabled=True,
        cutoff_cooldown_days=7,
        post_release_grace_hrs=0,
    )
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    cutoff_searched = [
        r for r in rows if r["search_kind"] == "cutoff" and r["action"] == "searched"
    ]
    assert len(cutoff_searched) == 0


@pytest.mark.asyncio()
@respx.mock
async def test_post_release_grace_reason_triggers_retry(
    seeded_instances: None,
) -> None:
    """Reason 'post-release grace (6h)': allowed in missing retry."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="post-release grace (6h)",
    )

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_non_release_reason_no_retry(
    seeded_instances: None,
) -> None:
    """Reason 'on cooldown (14d)': normal cooldown skip, no retry."""
    from houndarr.services.cooldown import record_search

    await record_search(1, 101, "episode")
    # Insert a non-release-timing reason in the log
    from .conftest import insert_search_log_row

    await insert_search_log_row(
        instance_id=1,
        item_id=101,
        item_type="episode",
        search_kind="missing",
        action="skipped",
        reason="on cooldown (14d)",
    )

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance()
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0


# ---------------------------------------------------------------------------
# Unreleased and grace window behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_unreleased_item_skipped(seeded_instances: None) -> None:
    """Future airDateUtc: skipped with 'not yet released'."""
    ep = {**_EPISODE_RECORD, "airDateUtc": _FUTURE_AIR_DATE}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance()
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    skipped = [r for r in rows if r["action"] == "skipped"]
    assert any("not yet released" in (r.get("reason") or "") for r in skipped)


@pytest.mark.asyncio()
@respx.mock
async def test_within_grace_period_skipped(
    seeded_instances: None,
) -> None:
    """Recently aired within grace: skipped."""
    from datetime import UTC, datetime, timedelta

    # Aired 1 hour ago, grace is 24 hours
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    ep = {**_EPISODE_RECORD, "airDateUtc": recent}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=24)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 0
    rows = await get_log_rows()
    skipped = [r for r in rows if r["action"] == "skipped"]
    assert any("post-release grace" in (r.get("reason") or "") for r in skipped)


@pytest.mark.asyncio()
@respx.mock
async def test_past_grace_period_eligible(
    seeded_instances: None,
) -> None:
    """Aired well past grace: eligible for search."""
    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=24)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_bypasses_grace(seeded_instances: None) -> None:
    """cycle_trigger='run_now': grace items searched."""
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    ep = {**_EPISODE_RECORD, "airDateUtc": recent}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=24)
    count = await run_instance_search(
        inst,
        MASTER_KEY,
        cycle_trigger="run_now",
    )

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_does_not_bypass_unreleased(
    seeded_instances: None,
) -> None:
    """run_now with future date: still skipped (pre-release is unconditional)."""
    ep = {**_EPISODE_RECORD, "airDateUtc": _FUTURE_AIR_DATE}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance()
    count = await run_instance_search(
        inst,
        MASTER_KEY,
        cycle_trigger="run_now",
    )

    assert count == 0


# ---------------------------------------------------------------------------
# Release-timing retry dispatch behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_dispatch_error(
    seeded_instances: None,
) -> None:
    """Retry hits dispatch error: logged as error."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="not yet released",
    )

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(500, text="error"),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0)
    await run_instance_search(inst, MASTER_KEY)

    rows = await get_log_rows()
    errors = [r for r in rows if r["action"] == "error" and r["search_kind"] == "missing"]
    assert len(errors) >= 1


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_records_cooldown(
    seeded_instances: None,
) -> None:
    """Successful retry updates cooldown record."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="not yet released",
    )

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["search_kind"] == "missing"]
    assert len(searched) >= 1


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_increments_count(
    seeded_instances: None,
) -> None:
    """Successful retry counts toward the searched total."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="not yet released",
    )

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0, batch_size=1)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_release_timing_retry_fires_when_sentinel_primed(
    seeded_instances: None,
) -> None:
    """Skip-log sentinel must not suppress the release-timing retry.

    Simulates a prior cycle that already logged a cooldown skip (the
    sentinel holds an entry for ``(instance, item, missing, cooldown)``).
    The retry path lives above the sentinel-gated skip-write and must
    still dispatch and record a ``searched`` row.
    """
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="not yet released",
    )

    # Prime the sentinel as if a previous cycle already logged the skip.
    primed = await should_log_skip((1, 101, "missing", "cooldown"))
    assert primed is True
    second = await should_log_skip((1, 101, "missing", "cooldown"))
    assert second is False  # sanity: second call is suppressed

    ep = {**_EPISODE_RECORD, "airDateUtc": "2023-09-01T00:00:00Z"}
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([ep])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    inst = _sonarr_instance(post_release_grace_hrs=0, batch_size=1)
    count = await run_instance_search(inst, MASTER_KEY)

    assert count == 1
    rows = await get_log_rows()
    searched = [r for r in rows if r["action"] == "searched" and r["item_id"] == 101]
    assert len(searched) == 1

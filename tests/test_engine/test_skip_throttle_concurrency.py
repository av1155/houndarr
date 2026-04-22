"""Concurrency check for the PR 0 skip-throttle sentinel.

Context
-------

Plan PR 0 proposes denoising the engine's per-cycle cooldown-skip writes to at
most one row per ``(instance_id, item_id, search_kind, reason-bucket)`` per
24 hours.  This test verifies the *concurrency* behavior of that sentinel under
the realistic case where two passes against the same instance run
near-simultaneously against the same cooldown item.

Chosen implementation
---------------------

**In-memory dict keyed by ``(instance_id, item_id, search_kind, reason_bucket)``
with an ``asyncio.Lock`` held across check-then-write.**  The dict avoids a v13
schema migration and keeps the de-noise purely an engine concern; the lock
serializes the check-then-write so two concurrent passes inside the same
process can't both bypass the sentinel.  Dict entries age out on a 24-hour TTL
and are reset on process restart, which is fine because a cold start has no
accumulated noise anyway.

What this test does
-------------------

Seeds an item into ``cooldowns`` so it is on-cooldown for any missing pass,
then fires two ``_run_search_pass`` invocations concurrently via
``asyncio.gather`` against the same candidate.  Asserts the expected
post-sentinel skip-row count in ``search_log``.

Per the user instruction accompanying this test: engine code is NOT modified.
The assertion reflects what the chosen sentinel design *should* produce.  A
failure here against current (pre-sentinel) code documents the structural
noise; it is the observation, not a bug to fix in this PR.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.engine.adapters import get_adapter
from houndarr.engine.candidates import SearchCandidate
from houndarr.engine.config.search_pass import SearchPassConfig
from houndarr.engine.search_loop import _run_search_pass
from houndarr.services.cooldown import record_search
from houndarr.services.instances import InstanceType
from tests.test_engine.conftest import make_instance, seeded_instances  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_ITEM_ID = 101
_ITEM_TYPE = "episode"
_INSTANCE_ID = 1  # Sonarr (from seeded_instances)


@pytest_asyncio.fixture()
async def cooldowned_item(seeded_instances: None) -> AsyncGenerator[None, None]:  # noqa: F811
    """Pre-seed a cooldowns row so ``is_on_cooldown`` returns True for the
    canonical ``(instance_id=1, item_id=101, item_type='episode')`` tuple used
    throughout this module.
    """
    await record_search(_INSTANCE_ID, _ITEM_ID, _ITEM_TYPE)
    yield


# ---------------------------------------------------------------------------
# The concurrency test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_concurrent_passes_produce_at_most_one_skip_row(
    cooldowned_item: None,
) -> None:
    """Two near-simultaneous missing passes against the same cooldown item
    should produce at most ONE ``action='skipped'`` row in ``search_log``.

    Against current engine code (no sentinel) this asserts and will fail with
    2 rows written; the structural noise PR 0 is intended to eliminate.
    """
    instance = make_instance(
        instance_id=_INSTANCE_ID,
        itype=InstanceType.sonarr,
        batch_size=5,
        hourly_cap=10,
        cooldown_days=14,
    )
    adapter = get_adapter(instance.core.type)

    # One pre-built candidate that matches the cooldowned item.  Both passes
    # pull from the same fetch_fn and hit is_on_cooldown -> True, then fall
    # through to the cooldown skip-write path at search_loop.py:443-459.
    candidate = SearchCandidate(
        item_id=_ITEM_ID,
        item_type=_ITEM_TYPE,
        label="Cooldowned Show S01E01",
        unreleased_reason=None,
        group_key=None,
        search_payload={"seriesId": 1, "episodeIds": [_ITEM_ID]},
    )

    # Raw item is opaque here; adapt_fn ignores it and returns the pre-built
    # candidate.  fetch_fn returns exactly one raw item per call.
    _raw_item: dict[str, Any] = {"id": _ITEM_ID}

    async def fetch_one(page: int, page_size: int) -> list[Any]:  # noqa: ARG001
        return [_raw_item]

    def adapt_passthrough(item: Any, instance: Any) -> SearchCandidate:  # noqa: ARG001
        return candidate

    # dispatch_fn must be awaitable; it should NEVER be called in this test
    # because the item is on cooldown and the pass should skip-write instead
    # of dispatching.  AsyncMock makes an accidental call visible.
    dispatch_mock = AsyncMock(return_value=None)

    async def run_one_pass(cycle_label: str) -> None:
        await _run_search_pass(
            instance,
            adapter,
            SearchPassConfig(
                adapt_fn=adapt_passthrough,
                dispatch_fn=dispatch_mock,
                fetch_fn=fetch_one,
                search_kind="missing",
                batch_size=5,
                hourly_cap=10,
                cooldown_days=14,
                page_size=10,
                scan_budget=10,
                cycle_id=f"cycle-{cycle_label}",
                cycle_trigger="scheduled",
                start_page=1,
                total_fn=None,
            ),
        )

    # asyncio.gather schedules both coroutines on the same event loop; they
    # interleave at every await point, which is the realistic race shape
    # (two cycles from the same supervisor landing inside the same tick).
    await asyncio.gather(run_one_pass("A"), run_one_pass("B"))

    # Dispatch should not have fired; the item is on cooldown.
    assert dispatch_mock.await_count == 0, (
        "Cooldowned item was dispatched; engine cooldown check is broken or the test seed is wrong."
    )

    # Count cooldown-reason skip rows written for this item.
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT COUNT(*) FROM search_log
            WHERE instance_id = ?
              AND item_id     = ?
              AND item_type   = ?
              AND action      = 'skipped'
              AND reason LIKE 'on cooldown (%'
            """,
            (_INSTANCE_ID, _ITEM_ID, _ITEM_TYPE),
        ) as cur:
            row = await cur.fetchone()

    skip_count = int(row[0]) if row else 0

    # POST-SENTINEL expectation.  Current (pre-sentinel) code writes 2.
    assert skip_count <= 1, (
        f"Expected at most 1 cooldown skip row after 2 concurrent passes "
        f"against the same on-cooldown item; got {skip_count}. "
        f"This documents the per-cycle duplication PR 0 is intended to fix."
    )

"""Cooldown reconciliation service tests.

Targets :func:`houndarr.services.cooldown_reconcile.reconcile_cooldowns`
and the invariant it is meant to enforce downstream: on every /api/status
envelope after the supervisor's snapshot refresh has run,
``eligible + gated + unreleased <= monitored_total`` per instance.
Stale cooldown rows are the only accumulator that can break the
inequality, so reconcile's job is to keep the cooldowns table a
projection of the *arr's live wanted / upgrade-pool state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from houndarr.clients.base import ReconcileSets
from houndarr.database import get_db
from houndarr.services.cooldown_reconcile import reconcile_cooldowns

_ENC_KEY = "gAAAAABlX_fake_fernet_value_long_enough_to_pass_validation=="


@pytest_asyncio.fixture()
async def seeded_instance(db: None) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Seed one instance row so cooldowns FKs resolve."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            (1, "Sonarr Test", "sonarr", "http://sonarr:8989", _ENC_KEY),
        )
        await conn.commit()
    yield


async def _seed_cooldown(
    instance_id: int,
    item_id: int,
    item_type: str,
    search_kind: str = "missing",
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns"
            " (instance_id, item_id, item_type, search_kind, searched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (instance_id, item_id, item_type, search_kind, now),
        )
        await conn.commit()


async def _count_cooldowns(instance_id: int) -> int:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE instance_id = ?",
            (instance_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.asyncio()
async def test_empty_sets_skip_delete(seeded_instance: None) -> None:  # noqa: ARG001
    """An empty ReconcileSets is a hard skip: never wipe the table."""
    await _seed_cooldown(1, 100, "episode", "missing")
    await _seed_cooldown(1, 200, "episode", "cutoff")
    removed = await reconcile_cooldowns(1, ReconcileSets.empty())
    assert removed == 0
    assert await _count_cooldowns(1) == 2


@pytest.mark.asyncio()
async def test_keeps_matching_rows(seeded_instance: None) -> None:  # noqa: ARG001
    """Rows whose (item_type, item_id) is in the matching set stay."""
    await _seed_cooldown(1, 100, "episode", "missing")
    await _seed_cooldown(1, 200, "episode", "cutoff")
    sets = ReconcileSets(
        missing=frozenset({("episode", 100)}),
        cutoff=frozenset({("episode", 200)}),
        upgrade=frozenset(),
    )
    removed = await reconcile_cooldowns(1, sets)
    assert removed == 0
    assert await _count_cooldowns(1) == 2


@pytest.mark.asyncio()
async def test_deletes_orphan_rows(seeded_instance: None) -> None:  # noqa: ARG001
    """Rows not in the matching set get deleted."""
    await _seed_cooldown(1, 100, "episode", "missing")  # orphan
    await _seed_cooldown(1, 101, "episode", "missing")  # keeper
    await _seed_cooldown(1, 200, "episode", "cutoff")  # orphan
    sets = ReconcileSets(
        missing=frozenset({("episode", 101)}),
        cutoff=frozenset(),
        upgrade=frozenset(),
    )
    removed = await reconcile_cooldowns(1, sets)
    assert removed == 2
    assert await _count_cooldowns(1) == 1


@pytest.mark.asyncio()
async def test_search_kind_scoped_to_pass(seeded_instance: None) -> None:  # noqa: ARG001
    """A row tagged 'missing' matches the missing set even if the cutoff
    set contains the same (item_type, item_id)."""
    await _seed_cooldown(1, 100, "episode", "missing")
    sets = ReconcileSets(
        missing=frozenset(),  # empty: 100 is orphaned from missing
        cutoff=frozenset({("episode", 100)}),  # present here but wrong kind
        upgrade=frozenset(),
    )
    removed = await reconcile_cooldowns(1, sets)
    assert removed == 1
    assert await _count_cooldowns(1) == 0


@pytest.mark.asyncio()
async def test_context_synth_id_kept(seeded_instance: None) -> None:  # noqa: ARG001
    """Season-context synthetic negative id survives reconcile when the
    adapter unions it into the pass set."""
    synth = -1234  # -(series_id * 1000 + season_number) for (1, 234)
    await _seed_cooldown(1, synth, "episode", "missing")
    sets = ReconcileSets(
        missing=frozenset({("episode", synth)}),
        cutoff=frozenset(),
        upgrade=frozenset(),
    )
    removed = await reconcile_cooldowns(1, sets)
    assert removed == 0
    assert await _count_cooldowns(1) == 1


@pytest.mark.asyncio()
async def test_batched_delete_many_rows(seeded_instance: None) -> None:  # noqa: ARG001
    """Deleting more than the batch size (500) still succeeds."""
    # Seed 600 orphan rows.
    tasks = [_seed_cooldown(1, i, "episode", "missing") for i in range(1, 601)]
    await asyncio.gather(*tasks)
    sets = ReconcileSets(
        missing=frozenset(),
        cutoff=frozenset(),
        upgrade=frozenset(),
    )
    # Empty sets skip delete entirely, so use a keeper to bypass the
    # is_empty sentinel path while still orphaning all seeded rows.
    sets = ReconcileSets(
        missing=frozenset({("episode", 10_000)}),
        cutoff=frozenset(),
        upgrade=frozenset(),
    )
    removed = await reconcile_cooldowns(1, sets)
    assert removed == 600
    assert await _count_cooldowns(1) == 0

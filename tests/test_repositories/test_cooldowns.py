"""Pinning tests for the cooldowns-repository SQL boundary.

Locks the contract of ``exists_active_cooldown``,
``upsert_cooldown``, and ``delete_cooldowns_for_instance`` plus the
service-layer delegators that keep
:mod:`houndarr.services.cooldown` as the stable import path for the
engine hot loop.  Each case below covers one boundary the delegation
has to preserve: empty table, fresh insert, upsert replace,
cooldown-days short-circuit, window-edge filter, per-instance delete
scope, and symmetric delegation with the service wrappers.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.engine.candidates import ItemType
from houndarr.repositories import cooldowns as repo
from houndarr.value_objects import ItemRef


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Insert two stub instance rows so FK constraints are satisfied."""
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
                (2, "Radarr Test", "radarr", "http://radarr:7878"),
            ],
        )
        await conn.commit()
    yield


async def _count_cooldowns(instance_id: int) -> int:
    """Count cooldown rows for *instance_id* directly against SQL."""
    async with (
        get_db() as conn,
        conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE instance_id = ?",
            (instance_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_exists_active_cooldown_false_on_empty_table(seeded_instances: None) -> None:
    """Empty cooldowns table returns False, no row present."""
    ref = ItemRef(1, 42, ItemType.episode)
    assert await repo.exists_active_cooldown(ref, cooldown_days=7) is False


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_exists_active_cooldown_short_circuits_on_disabled_cooldown(
    seeded_instances: None,
) -> None:
    """cooldown_days <= 0 returns False without touching the database."""
    ref = ItemRef(1, 42, ItemType.episode)
    # Even with an active record present, zero days disables the check.
    await repo.upsert_cooldown(ref, "missing")
    assert await repo.exists_active_cooldown(ref, cooldown_days=0) is False
    assert await repo.exists_active_cooldown(ref, cooldown_days=-3) is False


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_exists_active_cooldown_true_after_upsert(seeded_instances: None) -> None:
    """A just-upserted cooldown reads as active within the configured window."""
    ref = ItemRef(1, 42, ItemType.episode)
    await repo.upsert_cooldown(ref, "missing")
    assert await repo.exists_active_cooldown(ref, cooldown_days=7) is True


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_exists_active_cooldown_expired_row_returns_false(
    seeded_instances: None,
) -> None:
    """A cooldown older than the window is no longer active."""
    ref = ItemRef(1, 42, ItemType.episode)
    # Seed a stale timestamp directly (10 days ago).
    stale_time = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at)"
            " VALUES (?, ?, ?, ?)",
            (ref.instance_id, ref.item_id, ref.item_type.value, stale_time),
        )
        await conn.commit()

    assert await repo.exists_active_cooldown(ref, cooldown_days=7) is False


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_upsert_cooldown_inserts_first_time(seeded_instances: None) -> None:
    """First upsert creates a row; count goes from 0 to 1."""
    ref = ItemRef(1, 42, ItemType.episode)
    assert await _count_cooldowns(1) == 0
    await repo.upsert_cooldown(ref, "missing")
    assert await _count_cooldowns(1) == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_upsert_cooldown_replaces_on_repeat(seeded_instances: None) -> None:
    """Repeated upsert keeps the row count at 1 (in-place UPDATE)."""
    ref = ItemRef(1, 42, ItemType.episode)
    await repo.upsert_cooldown(ref, "missing")
    await repo.upsert_cooldown(ref, "missing")
    await repo.upsert_cooldown(ref, "missing")
    assert await _count_cooldowns(1) == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_upsert_cooldown_distinct_items_coexist(seeded_instances: None) -> None:
    """Different (item_id, item_type) triples share an instance without conflict."""
    await repo.upsert_cooldown(ItemRef(1, 42, ItemType.episode), "missing")
    await repo.upsert_cooldown(ItemRef(1, 43, ItemType.episode), "missing")
    await repo.upsert_cooldown(ItemRef(1, 42, ItemType.movie), "missing")
    assert await _count_cooldowns(1) == 3


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_cooldowns_for_instance_returns_row_count(
    seeded_instances: None,
) -> None:
    """delete_cooldowns_for_instance returns the number of rows removed."""
    await repo.upsert_cooldown(ItemRef(1, 1, ItemType.episode), "missing")
    await repo.upsert_cooldown(ItemRef(1, 2, ItemType.episode), "missing")
    await repo.upsert_cooldown(ItemRef(1, 3, ItemType.episode), "missing")

    deleted = await repo.delete_cooldowns_for_instance(1)
    assert deleted == 3
    assert await _count_cooldowns(1) == 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_cooldowns_for_instance_returns_zero_when_empty(
    seeded_instances: None,
) -> None:
    """delete_cooldowns_for_instance returns 0 when no rows match."""
    deleted = await repo.delete_cooldowns_for_instance(1)
    assert deleted == 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_cooldowns_is_scoped_to_one_instance(seeded_instances: None) -> None:
    """Deleting cooldowns for instance A leaves instance B untouched."""
    await repo.upsert_cooldown(ItemRef(1, 1, ItemType.episode), "missing")
    await repo.upsert_cooldown(ItemRef(2, 1, ItemType.movie), "missing")

    await repo.delete_cooldowns_for_instance(1)
    assert await _count_cooldowns(1) == 0
    assert await _count_cooldowns(2) == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_is_on_cooldown_ref_delegates(seeded_instances: None) -> None:
    """services.cooldown.is_on_cooldown_ref returns the same bool as the repo."""
    from houndarr.services.cooldown import is_on_cooldown_ref as svc_check

    ref = ItemRef(1, 42, ItemType.episode)
    await repo.upsert_cooldown(ref, "missing")

    assert await svc_check(ref, cooldown_days=7) == await repo.exists_active_cooldown(
        ref, cooldown_days=7
    )


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_record_search_ref_delegates(seeded_instances: None) -> None:
    """services.cooldown.record_search_ref writes through the repository."""
    from houndarr.services.cooldown import record_search_ref as svc_record

    ref = ItemRef(1, 42, ItemType.episode)
    await svc_record(ref, "missing")
    assert await repo.exists_active_cooldown(ref, cooldown_days=7) is True


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_clear_cooldowns_delegates(seeded_instances: None) -> None:
    """services.cooldown.clear_cooldowns returns the same count as the repo."""
    from houndarr.services.cooldown import clear_cooldowns as svc_clear

    await repo.upsert_cooldown(ItemRef(1, 1, ItemType.episode), "missing")
    await repo.upsert_cooldown(ItemRef(1, 2, ItemType.episode), "missing")

    assert await svc_clear(1) == 2

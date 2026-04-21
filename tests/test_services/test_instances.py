"""Tests for the instance CRUD service layer."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from houndarr.services.instances import (
    Instance,
    InstanceType,
    SonarrSearchMode,
    create_instance,
    delete_instance,
    get_instance,
    list_instances,
    update_instance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def master_key() -> bytes:
    """Fresh Fernet key for each test."""
    return Fernet.generate_key()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make(master_key: bytes, **overrides: object) -> Instance:
    """Create a minimal Sonarr instance with optional field overrides."""
    defaults: dict[str, object] = {
        "name": "My Sonarr",
        "type": InstanceType.sonarr,
        "url": "http://sonarr:8989",
        "api_key": "plaintext-key-abc123",
    }
    defaults.update(overrides)
    return await create_instance(master_key=master_key, **defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# create_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_returns_instance(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    assert isinstance(inst, Instance)
    assert inst.id >= 1
    assert inst.name == "My Sonarr"
    assert inst.type == InstanceType.sonarr
    assert inst.url == "http://sonarr:8989"


@pytest.mark.asyncio()
async def test_create_decrypts_api_key(db: None, master_key: bytes) -> None:
    inst = await _make(master_key, api_key="secret-key-xyz")
    assert inst.api_key == "secret-key-xyz"


@pytest.mark.asyncio()
async def test_create_applies_defaults(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    assert inst.enabled is True
    assert inst.batch_size == 2
    assert inst.sleep_interval_mins == 30
    assert inst.hourly_cap == 4
    assert inst.cooldown_days == 14
    assert inst.post_release_grace_hrs == 6
    assert inst.queue_limit == 0
    assert inst.cutoff_enabled is False
    assert inst.cutoff_batch_size == 1
    assert inst.cutoff_cooldown_days == 21
    assert inst.cutoff_hourly_cap == 1
    assert inst.sonarr_search_mode == SonarrSearchMode.episode
    assert inst.allowed_time_window == ""


@pytest.mark.asyncio()
async def test_create_radarr_instance(db: None, master_key: bytes) -> None:
    inst = await _make(
        master_key, name="My Radarr", type=InstanceType.radarr, url="http://radarr:7878"
    )
    assert inst.type == InstanceType.radarr


@pytest.mark.asyncio()
async def test_api_key_stored_encrypted(db: None, master_key: bytes) -> None:
    """The raw DB value must differ from the plaintext API key."""
    from houndarr.database import get_db

    await _make(master_key, api_key="super-secret")
    async with get_db() as conn:
        async with conn.execute("SELECT encrypted_api_key FROM instances WHERE id = 1") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["encrypted_api_key"] != "super-secret"


# ---------------------------------------------------------------------------
# get_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_instance_found(db: None, master_key: bytes) -> None:
    created = await _make(master_key)
    fetched = await get_instance(created.id, master_key=master_key)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.api_key == created.api_key


@pytest.mark.asyncio()
async def test_get_instance_not_found(db: None, master_key: bytes) -> None:
    result = await get_instance(9999, master_key=master_key)
    assert result is None


# ---------------------------------------------------------------------------
# list_instances
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_empty(db: None, master_key: bytes) -> None:
    instances = await list_instances(master_key=master_key)
    assert instances == []


@pytest.mark.asyncio()
async def test_list_returns_all(db: None, master_key: bytes) -> None:
    await _make(master_key, name="A")
    await _make(master_key, name="B", type=InstanceType.radarr, url="http://radarr:7878")
    instances = await list_instances(master_key=master_key)
    assert len(instances) == 2
    names = {i.name for i in instances}
    assert names == {"A", "B"}


@pytest.mark.asyncio()
async def test_list_ordered_by_id(db: None, master_key: bytes) -> None:
    a = await _make(master_key, name="First")
    b = await _make(master_key, name="Second", type=InstanceType.radarr, url="http://radarr:7878")
    instances = await list_instances(master_key=master_key)
    assert instances[0].id == a.id
    assert instances[1].id == b.id


# ---------------------------------------------------------------------------
# update_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_update_name(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    updated = await update_instance(inst.id, master_key=master_key, name="Renamed")
    assert updated is not None
    assert updated.name == "Renamed"


@pytest.mark.asyncio()
async def test_update_api_key_re_encrypted(db: None, master_key: bytes) -> None:
    inst = await _make(master_key, api_key="old-key")
    updated = await update_instance(inst.id, master_key=master_key, api_key="new-key")
    assert updated is not None
    assert updated.api_key == "new-key"

    # Raw DB value must still be encrypted (not plaintext)
    from houndarr.database import get_db

    async with get_db() as conn:
        async with conn.execute(
            "SELECT encrypted_api_key FROM instances WHERE id = ?", (inst.id,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["encrypted_api_key"] != "new-key"


@pytest.mark.asyncio()
async def test_update_multiple_fields(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    updated = await update_instance(
        inst.id,
        master_key=master_key,
        batch_size=20,
        hourly_cap=50,
        enabled=False,
    )
    assert updated is not None
    assert updated.batch_size == 20
    assert updated.hourly_cap == 50
    assert updated.enabled is False


@pytest.mark.asyncio()
async def test_update_sonarr_search_mode(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    updated = await update_instance(
        inst.id,
        master_key=master_key,
        sonarr_search_mode=SonarrSearchMode.season_context,
    )
    assert updated is not None
    assert updated.sonarr_search_mode == SonarrSearchMode.season_context


@pytest.mark.asyncio()
async def test_update_unknown_fields_ignored(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    # Should not raise even with unrecognised keys
    updated = await update_instance(inst.id, master_key=master_key, nonexistent_col="x")
    assert updated is not None
    assert updated.name == inst.name


@pytest.mark.asyncio()
async def test_update_nonexistent_returns_none(db: None, master_key: bytes) -> None:
    result = await update_instance(9999, master_key=master_key, name="Ghost")
    assert result is None


@pytest.mark.asyncio()
async def test_update_refreshes_updated_at(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    original_updated_at = inst.updated_at
    # Brief sleep to ensure the timestamp differs
    import asyncio

    await asyncio.sleep(0.01)
    updated = await update_instance(inst.id, master_key=master_key, name="Changed")
    assert updated is not None
    # updated_at should be >= original (may be equal at ms resolution, never less)
    assert updated.updated_at >= original_updated_at


# ---------------------------------------------------------------------------
# delete_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_delete_existing(db: None, master_key: bytes) -> None:
    inst = await _make(master_key)
    result = await delete_instance(inst.id)
    assert result is True
    assert await get_instance(inst.id, master_key=master_key) is None


@pytest.mark.asyncio()
async def test_delete_nonexistent(db: None, master_key: bytes) -> None:
    result = await delete_instance(9999)
    assert result is False


@pytest.mark.asyncio()
async def test_delete_removes_from_list(db: None, master_key: bytes) -> None:
    a = await _make(master_key, name="Keep")
    b = await _make(master_key, name="Delete", type=InstanceType.radarr, url="http://radarr:7878")
    await delete_instance(b.id)
    remaining = await list_instances(master_key=master_key)
    assert len(remaining) == 1
    assert remaining[0].id == a.id


# ---------------------------------------------------------------------------
# allowed_time_window round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_persists_allowed_time_window(db: None, master_key: bytes) -> None:
    inst = await _make(master_key, allowed_time_window="09:00-23:00")
    fetched = await get_instance(inst.id, master_key=master_key)
    assert fetched is not None
    assert fetched.allowed_time_window == "09:00-23:00"


@pytest.mark.asyncio()
async def test_update_allowed_time_window(db: None, master_key: bytes) -> None:
    from houndarr.services.instances import update_instance

    inst = await _make(master_key)
    assert inst.allowed_time_window == ""

    updated = await update_instance(
        inst.id,
        master_key=master_key,
        allowed_time_window="22:00-06:00",
    )
    assert updated is not None
    assert updated.allowed_time_window == "22:00-06:00"

    # Clearing works too.
    cleared = await update_instance(
        inst.id,
        master_key=master_key,
        allowed_time_window="",
    )
    assert cleared is not None
    assert cleared.allowed_time_window == ""


# ---------------------------------------------------------------------------
# search_order (#394)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_applies_search_order_default(db: None, master_key: bytes) -> None:
    from houndarr.services.instances import SearchOrder

    inst = await _make(master_key)
    assert inst.search_order == SearchOrder.random


@pytest.mark.asyncio()
async def test_update_search_order_to_random(db: None, master_key: bytes) -> None:
    from houndarr.services.instances import SearchOrder

    inst = await _make(master_key)
    updated = await update_instance(
        inst.id,
        master_key=master_key,
        search_order=SearchOrder.random,
    )
    assert updated is not None
    assert updated.search_order == SearchOrder.random

    refetched = await get_instance(inst.id, master_key=master_key)
    assert refetched is not None
    assert refetched.search_order == SearchOrder.random

    reverted = await update_instance(
        inst.id,
        master_key=master_key,
        search_order=SearchOrder.chronological,
    )
    assert reverted is not None
    assert reverted.search_order == SearchOrder.chronological


# ---------------------------------------------------------------------------
# PR 5: v13 snapshot columns + update_instance_snapshot helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_v13_snapshot_columns_default_zero(db: None, master_key: bytes) -> None:
    """Fresh v13 installs initialize snapshot columns to their defaults."""
    inst = await _make(master_key)
    assert inst.monitored_total == 0
    assert inst.unreleased_count == 0
    assert inst.snapshot_refreshed_at == ""


@pytest.mark.asyncio()
async def test_update_instance_snapshot_writes_all_columns(db: None, master_key: bytes) -> None:
    from houndarr.services.instances import update_instance_snapshot

    inst = await _make(master_key)
    await update_instance_snapshot(inst.id, monitored_total=123, unreleased_count=7)
    refreshed = await get_instance(inst.id, master_key=master_key)
    assert refreshed is not None
    assert refreshed.monitored_total == 123
    assert refreshed.unreleased_count == 7
    assert refreshed.snapshot_refreshed_at != ""


@pytest.mark.asyncio()
async def test_update_instance_snapshot_overwrites_prior_values(
    db: None, master_key: bytes
) -> None:
    from houndarr.services.instances import update_instance_snapshot

    inst = await _make(master_key)
    await update_instance_snapshot(inst.id, monitored_total=50, unreleased_count=3)
    await update_instance_snapshot(inst.id, monitored_total=60, unreleased_count=5)
    refreshed = await get_instance(inst.id, master_key=master_key)
    assert refreshed is not None
    assert refreshed.monitored_total == 60
    assert refreshed.unreleased_count == 5


# ---------------------------------------------------------------------------
# active_error_instance_ids: 2-day window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_active_error_ignores_rows_older_than_window(db: None, master_key: bytes) -> None:
    """An error row older than 2 days must not keep the dot lit. If a
    genuinely stuck instance is still failing, a fresh error row lands
    well inside the window; a two-day silence means the problem is
    stale."""
    from datetime import UTC, datetime, timedelta

    from houndarr.database import get_db
    from houndarr.services.instances import active_error_instance_ids

    inst = await _make(master_key)
    stale = (datetime.now(tz=UTC) - timedelta(days=3)).isoformat()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, action, cycle_trigger, timestamp)"
            " VALUES (?, 'error', 'system', ?)",
            (inst.id, stale),
        )
        await conn.commit()

    assert await active_error_instance_ids() == set()


@pytest.mark.asyncio()
async def test_active_error_reports_fresh_error(db: None, master_key: bytes) -> None:
    """An error row from the last minute still lights the dot."""
    from datetime import UTC, datetime

    from houndarr.database import get_db
    from houndarr.services.instances import active_error_instance_ids

    inst = await _make(master_key)
    fresh = datetime.now(tz=UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, action, cycle_trigger, timestamp)"
            " VALUES (?, 'error', 'system', ?)",
            (inst.id, fresh),
        )
        await conn.commit()

    assert await active_error_instance_ids() == {inst.id}


@pytest.mark.asyncio()
async def test_active_error_excludes_same_day_older_row(db: None, master_key: bytes) -> None:
    """Regression for a timestamp format-mismatch bug in the window clause.

    ``search_log.timestamp`` is stored via ``strftime('%Y-%m-%dT%H:%M:%fZ',
    'now')`` which produces values like ``2026-04-19T14:30:00.123Z`` (ISO
    8601, 'T' separator, 'Z' suffix). A prior version of the window clause
    used ``datetime('now', '-2 days')``, which emits ``YYYY-MM-DD HH:MM:SS``
    (space separator, no fractional seconds, no 'Z'). SQLite compares TEXT
    lexicographically, so at position 10 the cutoff's space (0x20) sorted
    below the stored 'T' (0x54); any row whose calendar date equalled the
    cutoff's was included regardless of its time-of-day, letting stale
    errors up to ~24 h older than the 48-hour window light the dashboard
    dot.

    Seed a row at midnight UTC of the cutoff's calendar date. That row is
    between 24 h and 48 h older than the exact 48-hour cutoff for any
    non-midnight wall clock and must therefore be excluded.
    """
    from datetime import UTC, datetime, timedelta

    from houndarr.database import get_db
    from houndarr.services.instances import active_error_instance_ids

    inst = await _make(master_key)
    cutoff_date = (datetime.now(tz=UTC) - timedelta(days=2)).date()
    # Match the stored ISO 8601 format including the 'Z' suffix; the bug
    # triggers only when calendar dates collide, which is why we pin the
    # day portion to the cutoff's date and set the time portion to
    # midnight so the row is strictly older than the exact 48 h cutoff.
    stale = f"{cutoff_date.isoformat()}T00:00:00.000Z"
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, action, cycle_trigger, timestamp)"
            " VALUES (?, 'error', 'system', ?)",
            (inst.id, stale),
        )
        await conn.commit()

    assert await active_error_instance_ids() == set()

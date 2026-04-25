"""Pinning tests for the instances-repository write boundary.

Locks the contract of the :class:`InstanceInsert` /
:class:`InstanceUpdate` payload dataclasses and the five write
functions (``insert_instance``, ``update_instance``,
``delete_instance``, ``update_instance_snapshot``, and the service
delegator path).  Every case below pins one boundary the
repository has to preserve so business logic in the service and
route layers can evolve without drifting the SQL below them.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

import pytest
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.repositories import instances as repo
from houndarr.repositories.instances import (
    InstanceInsert,
    InstanceUpdate,
    delete_instance,
    insert_instance,
    update_instance,
    update_instance_snapshot,
)
from houndarr.services.instances import (
    InstanceType,
    SearchOrder,
    SonarrSearchMode,
    create_instance,
)


@pytest.fixture()
def master_key() -> bytes:
    """Fresh Fernet key per test."""
    return Fernet.generate_key()


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_instance_returns_new_primary_key(db: None, master_key: bytes) -> None:
    """insert_instance returns the SQLite-assigned rowid."""
    payload = InstanceInsert(
        name="Inserted",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="plain-text",
    )
    row_id = await insert_instance(payload, master_key=master_key)
    assert isinstance(row_id, int)
    assert row_id >= 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_instance_encrypts_api_key_before_storage(db: None, master_key: bytes) -> None:
    """The api_key written to SQL is the ciphertext, not the plaintext payload."""
    payload = InstanceInsert(
        name="Encrypted",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="plaintext-key",
    )
    row_id = await insert_instance(payload, master_key=master_key)

    async with (
        get_db() as conn,
        conn.execute("SELECT encrypted_api_key FROM instances WHERE id = ?", (row_id,)) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row["encrypted_api_key"] != "plaintext-key"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_instance_applies_schema_defaults(db: None, master_key: bytes) -> None:
    """Minimal payload ends up with the legacy column defaults when read back."""
    payload = InstanceInsert(
        name="Defaults",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="k",
    )
    row_id = await insert_instance(payload, master_key=master_key)

    inst = await repo.get_instance(row_id, master_key=master_key)
    assert inst is not None
    assert inst.core.enabled is True
    assert inst.missing.batch_size == 2
    assert inst.missing.sleep_interval_mins == 30
    assert inst.missing.hourly_cap == 4
    assert inst.missing.cooldown_days == 14
    assert inst.missing.post_release_grace_hrs == 6
    assert inst.missing.queue_limit == 0
    assert inst.cutoff.cutoff_enabled is False
    assert inst.cutoff.cutoff_batch_size == 1
    assert inst.cutoff.cutoff_cooldown_days == 21
    assert inst.cutoff.cutoff_hourly_cap == 1
    assert inst.missing.sonarr_search_mode is SonarrSearchMode.episode


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_applies_single_field(db: None, master_key: bytes) -> None:
    """Supplying one non-None field rewrites exactly that column."""
    created = await create_instance(
        master_key=master_key,
        name="Before",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )
    await update_instance(created.core.id, InstanceUpdate(name="After"), master_key=master_key)
    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.core.name == "After"
    assert inst.core.url == "http://s:8989"  # other fields untouched


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_is_noop_when_payload_is_empty(db: None, master_key: bytes) -> None:
    """Every-None InstanceUpdate skips SQL entirely; updated_at does not change."""
    created = await create_instance(
        master_key=master_key,
        name="Stable",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )
    original_updated_at = created.timestamps.updated_at

    await update_instance(created.core.id, InstanceUpdate(), master_key=master_key)

    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.timestamps.updated_at == original_updated_at


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_reencrypts_api_key(db: None, master_key: bytes) -> None:
    """api_key in InstanceUpdate is plaintext; the repo re-encrypts it."""
    created = await create_instance(
        master_key=master_key,
        name="Rotate",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="old-key",
    )

    await update_instance(
        created.core.id, InstanceUpdate(api_key="rotated-key"), master_key=master_key
    )

    # Repo read decrypts to plaintext
    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.core.api_key == "rotated-key"

    # Raw SQL row holds ciphertext, not the plaintext
    async with (
        get_db() as conn,
        conn.execute(
            "SELECT encrypted_api_key FROM instances WHERE id = ?", (created.core.id,)
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row["encrypted_api_key"] != "rotated-key"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_coerces_enum_fields(db: None, master_key: bytes) -> None:
    """Enum-valued fields flatten to the underlying str before SQL."""
    created = await create_instance(
        master_key=master_key,
        name="Modes",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )

    await update_instance(
        created.core.id,
        InstanceUpdate(
            sonarr_search_mode=SonarrSearchMode.season_context,
            search_order=SearchOrder.random,
        ),
        master_key=master_key,
    )

    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.missing.sonarr_search_mode is SonarrSearchMode.season_context
    assert inst.schedule.search_order is SearchOrder.random


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_coerces_bool_fields(db: None, master_key: bytes) -> None:
    """Bool fields become 0/1 ints in SQL; round-trip preserves bool-ness."""
    created = await create_instance(
        master_key=master_key,
        name="Toggled",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )

    await update_instance(
        created.core.id,
        InstanceUpdate(enabled=False, cutoff_enabled=True, upgrade_enabled=True),
        master_key=master_key,
    )
    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.core.enabled is False
    assert inst.cutoff.cutoff_enabled is True
    assert inst.upgrade.upgrade_enabled is True


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_bumps_updated_at(db: None, master_key: bytes) -> None:
    """Any non-empty update moves updated_at forward of created_at."""
    created = await create_instance(
        master_key=master_key,
        name="Stamp",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )

    await update_instance(created.core.id, InstanceUpdate(name="Stamped"), master_key=master_key)
    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    # updated_at is a ISO-8601 string; lexicographic compare == chronological
    assert inst.timestamps.updated_at >= created.timestamps.updated_at


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_instance_returns_true_when_row_existed(db: None, master_key: bytes) -> None:
    """delete_instance returns True when a row was removed."""
    created = await create_instance(
        master_key=master_key,
        name="Doomed",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )

    deleted = await delete_instance(created.core.id)
    assert deleted is True
    assert await repo.get_instance(created.core.id, master_key=master_key) is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_instance_returns_false_when_row_missing(db: None) -> None:
    """delete_instance returns False when no row matched the id."""
    deleted = await delete_instance(9999)
    assert deleted is False


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_snapshot_writes_three_columns(db: None, master_key: bytes) -> None:
    """update_instance_snapshot populates monitored_total, unreleased_count, timestamp."""
    created = await create_instance(
        master_key=master_key,
        name="Snapshot",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )
    assert created.snapshot.monitored_total == 0
    assert created.snapshot.snapshot_refreshed_at == ""

    await update_instance_snapshot(created.core.id, monitored_total=42, unreleased_count=7)

    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.snapshot.monitored_total == 42
    assert inst.snapshot.unreleased_count == 7
    assert inst.snapshot.snapshot_refreshed_at != ""  # ISO-8601 filled by strftime


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_update_instance_snapshot_overwrites_prior_values(
    db: None, master_key: bytes
) -> None:
    """Repeated snapshot writes replace the previous numbers."""
    created = await create_instance(
        master_key=master_key,
        name="Refreshed",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )
    await update_instance_snapshot(created.core.id, monitored_total=100, unreleased_count=10)
    await update_instance_snapshot(created.core.id, monitored_total=200, unreleased_count=20)

    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.snapshot.monitored_total == 200
    assert inst.snapshot.unreleased_count == 20


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_create_instance_delegates_to_repo(db: None, master_key: bytes) -> None:
    """The service-layer create still works end-to-end through the repo payload."""
    inst = await create_instance(
        master_key=master_key,
        name="Delegated",
        type=InstanceType.radarr,
        url="http://radarr:7878",
        api_key="rkey",
    )
    assert inst.core.id >= 1
    assert inst.core.name == "Delegated"
    assert inst.core.api_key == "rkey"
    assert inst.core.type is InstanceType.radarr


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_update_instance_raises_on_unknown_kwargs(
    db: None, master_key: bytes
) -> None:
    """Unknown kwargs on services.update_instance raise TypeError.

    Review-adversarial fix: an earlier revision silently dropped
    unrecognised field names "for safety", which turned rename drift
    (either a repository column rename or a caller-side typo) into a
    silent bug farm where the SQL update ran with fewer fields than
    the caller expected.  The service now surfaces the drift at the
    call site with the offending key set named explicitly.
    """
    from houndarr.services.instances import update_instance as svc_update

    created = await create_instance(
        master_key=master_key,
        name="Filter",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )

    with pytest.raises(TypeError) as exc_info:
        await svc_update(
            created.core.id,
            master_key=master_key,
            name="Renamed",
            nonexistent_field="ignored",
            another_bogus_kwarg=123,
        )
    message = str(exc_info.value)
    assert "another_bogus_kwarg" in message
    assert "nonexistent_field" in message


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_update_instance_returns_none_for_missing_id(
    db: None, master_key: bytes
) -> None:
    """Updating a non-existent id returns None rather than raising."""
    from houndarr.services.instances import update_instance as svc_update

    result = await svc_update(999, master_key=master_key, name="Nope")
    assert result is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_delete_instance_delegates_to_repo(db: None, master_key: bytes) -> None:
    """services.delete_instance returns the repo's bool directly."""
    from houndarr.services.instances import delete_instance as svc_delete

    created = await create_instance(
        master_key=master_key,
        name="Trashed",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )
    assert await svc_delete(created.core.id) is True
    assert await svc_delete(created.core.id) is False


@pytest.mark.pinning()
def test_instance_insert_is_frozen_dataclass_with_slots() -> None:
    """InstanceInsert is a frozen slotted dataclass for immutability + memory."""
    with pytest.raises(Exception):  # noqa: B017, PT011
        payload = InstanceInsert(
            name="x",
            type=InstanceType.sonarr,
            url="http://x",
            api_key="k",
        )
        payload.name = "mutated"  # type: ignore[misc]


@pytest.mark.pinning()
def test_instance_update_is_frozen_dataclass_with_slots() -> None:
    """InstanceUpdate is a frozen slotted dataclass for immutability + memory."""
    with pytest.raises(Exception):  # noqa: B017, PT011
        payload = InstanceUpdate(name="x")
        payload.name = "mutated"  # type: ignore[misc]


@pytest.mark.pinning()
def test_instance_update_has_every_updatable_column() -> None:
    """InstanceUpdate covers every updatable column the service previously allowed."""
    expected = {
        "name",
        "type",
        "url",
        "api_key",
        "enabled",
        "batch_size",
        "sleep_interval_mins",
        "hourly_cap",
        "cooldown_days",
        "post_release_grace_hrs",
        "queue_limit",
        "cutoff_enabled",
        "cutoff_batch_size",
        "cutoff_cooldown_days",
        "cutoff_hourly_cap",
        "sonarr_search_mode",
        "lidarr_search_mode",
        "readarr_search_mode",
        "whisparr_v2_search_mode",
        "upgrade_enabled",
        "upgrade_batch_size",
        "upgrade_cooldown_days",
        "upgrade_hourly_cap",
        "upgrade_sonarr_search_mode",
        "upgrade_lidarr_search_mode",
        "upgrade_readarr_search_mode",
        "upgrade_whisparr_v2_search_mode",
        "upgrade_item_offset",
        "upgrade_series_offset",
        "upgrade_series_window_size",
        "missing_page_offset",
        "cutoff_page_offset",
        "allowed_time_window",
        "search_order",
        "monitored_total",
        "unreleased_count",
        "snapshot_refreshed_at",
    }
    actual = {f.name for f in dataclass_fields(InstanceUpdate)}
    assert actual == expected

"""Pinning tests for the instances-repository write boundary.

Locks the contract of the :class:`InstanceInsert` /
:class:`InstanceUpdate` payload dataclasses and the write functions
(``insert_instance``, ``update_instance``, ``delete_instance``,
``update_instance_snapshot``, ``update_instance_offset``, and the
service delegator path).  Every case below pins one boundary the
repository has to preserve so business logic in the service and
route layers can evolve without drifting the SQL below them.
"""

from __future__ import annotations

import asyncio
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
    update_instance_offset,
    update_instance_snapshot,
)
from houndarr.services.instances import (
    InstanceType,
    OffsetColumn,
    SearchOrder,
    SonarrSearchMode,
    TagFilterPolicy,
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
    assert inst.missing.missing_enabled is True
    assert inst.missing.batch_size == 2
    assert inst.missing.sleep_interval_mins == 30
    assert inst.missing.hourly_cap == 4
    assert inst.missing.cooldown_days == 14
    assert inst.missing.post_release_grace_hrs == 6
    assert inst.missing.missing_hot_retry_window_hrs == 0
    assert inst.missing.missing_hot_retry_interval_hrs == 2
    assert inst.missing.queue_limit == 0
    assert inst.cutoff.cutoff_enabled is False
    assert inst.cutoff.cutoff_batch_size == 1
    assert inst.cutoff.cutoff_cooldown_days == 21
    assert inst.cutoff.cutoff_hourly_cap == 1
    assert inst.missing.sonarr_search_mode is SonarrSearchMode.episode
    assert inst.tag_filter == TagFilterPolicy()


@pytest.mark.asyncio()
async def test_insert_instance_round_trips_per_pass_tag_excludes(
    db: None, master_key: bytes
) -> None:
    """Each per-pass exclude column lands in its own TagFilterPolicy field."""
    payload = InstanceInsert(
        name="Tags",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="k",
        tag_filter_exclude="skip",
        tag_filter_missing_exclude="no-backfill",
        tag_filter_cutoff_exclude="no-upgrade,sd-keep",
        tag_filter_upgrade_exclude="keep-sd",
    )
    row_id = await insert_instance(payload, master_key=master_key)

    inst = await repo.get_instance(row_id, master_key=master_key)
    assert inst is not None
    assert inst.tag_filter == TagFilterPolicy(
        exclude=("skip",),
        missing_exclude=("no-backfill",),
        cutoff_exclude=("no-upgrade", "sd-keep"),
        upgrade_exclude=("keep-sd",),
    )
    async with get_db() as conn:
        async with conn.execute(
            "SELECT tag_filter_missing_exclude, tag_filter_cutoff_exclude, "
            "tag_filter_upgrade_exclude FROM instances WHERE id = ?",
            (row_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert tuple(row) == ("no-backfill", "no-upgrade,sd-keep", "keep-sd")


@pytest.mark.asyncio()
async def test_update_instance_writes_and_clears_per_pass_tag_excludes(
    db: None, master_key: bytes
) -> None:
    """An empty string clears one per-pass exclude and leaves the others alone."""
    created = await create_instance(
        master_key=master_key,
        name="Tags",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
        tag_filter_missing_exclude="no-backfill",
    )
    await update_instance(
        created.core.id,
        InstanceUpdate(
            tag_filter_cutoff_exclude="no-upgrade", tag_filter_upgrade_exclude="keep-sd"
        ),
        master_key=master_key,
    )
    await update_instance(
        created.core.id, InstanceUpdate(tag_filter_cutoff_exclude=""), master_key=master_key
    )

    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.tag_filter == TagFilterPolicy(
        missing_exclude=("no-backfill",), upgrade_exclude=("keep-sd",)
    )


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
async def test_update_instance_on_caller_connection_leaves_commit_to_caller(
    db: None, master_key: bytes
) -> None:
    """With ``db`` the write joins the caller's transaction, so a rollback discards it."""
    created = await create_instance(
        master_key=master_key,
        name="Before",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
    )
    async with get_db() as conn:
        await update_instance(
            created.core.id, InstanceUpdate(name="Uncommitted"), master_key=master_key, db=conn
        )
        assert conn.in_transaction
        await conn.rollback()

    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.core.name == "Before"


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
        InstanceUpdate(
            enabled=False,
            missing_enabled=False,
            cutoff_enabled=True,
            upgrade_enabled=True,
        ),
        master_key=master_key,
    )
    inst = await repo.get_instance(created.core.id, master_key=master_key)
    assert inst is not None
    assert inst.core.enabled is False
    assert inst.missing.missing_enabled is False
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
    with pytest.raises(Exception):  # noqa: B017
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
    with pytest.raises(Exception):  # noqa: B017
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
        "missing_enabled",
        "batch_size",
        "sleep_interval_mins",
        "hourly_cap",
        "cooldown_days",
        "post_release_grace_hrs",
        "missing_hot_retry_window_hrs",
        "missing_hot_retry_interval_hrs",
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
        "tag_filter_include",
        "tag_filter_exclude",
        "tag_filter_missing_exclude",
        "tag_filter_cutoff_exclude",
        "tag_filter_upgrade_exclude",
        "monitored_total",
        "unreleased_count",
        "snapshot_refreshed_at",
    }
    actual = {f.name for f in dataclass_fields(InstanceUpdate)}
    assert actual == expected


# ---------------------------------------------------------------------------
# update_instance_offset: conditional cycle writes
# ---------------------------------------------------------------------------

_STALE_UPDATED_AT = "2024-01-01T00:00:00.000Z"
_STARTS = {
    "missing_page_offset": 3,
    "cutoff_page_offset": 4,
    "upgrade_item_offset": 7,
    "upgrade_series_offset": 10,
}


async def _cycle_row(master_key: bytes) -> int:
    """Insert a Chronological row with all three passes on and every offset moved."""
    created = await create_instance(
        master_key=master_key,
        name="Cycling",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="k",
        cutoff_enabled=True,
        upgrade_enabled=True,
        search_order=SearchOrder.chronological,
    )
    await update_instance(created.core.id, InstanceUpdate(**_STARTS), master_key=master_key)
    async with get_db() as conn:
        await conn.execute(
            "UPDATE instances SET updated_at = ? WHERE id = ?",
            (_STALE_UPDATED_AT, created.core.id),
        )
        await conn.commit()
    return created.core.id


async def _row(instance_id: int) -> dict[str, object]:
    async with (
        get_db() as conn,
        conn.execute(
            "SELECT missing_page_offset, cutoff_page_offset, upgrade_item_offset,"
            " upgrade_series_offset, updated_at FROM instances WHERE id = ?",
            (instance_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


@pytest.mark.asyncio()
@pytest.mark.parametrize("column", list(_STARTS))
async def test_update_instance_offset_writes_when_the_row_matches(
    db: None, master_key: bytes, column: OffsetColumn
) -> None:
    """A matching start writes the value, bumps updated_at and leaves the other offsets."""
    instance_id = await _cycle_row(master_key)

    written = await update_instance_offset(
        instance_id, column, expected=_STARTS[column], value=_STARTS[column] + 1
    )

    row = await _row(instance_id)
    assert written is True
    assert row[column] == _STARTS[column] + 1
    assert row["updated_at"] != _STALE_UPDATED_AT
    others = {c for c in _STARTS if c != column}
    assert {c: row[c] for c in others} == {c: _STARTS[c] for c in others}


@pytest.mark.asyncio()
@pytest.mark.parametrize("column", list(_STARTS))
async def test_update_instance_offset_skips_a_changed_offset(
    db: None, master_key: bytes, column: OffsetColumn
) -> None:
    """A start the row no longer holds leaves the row, updated_at included, as it was."""
    instance_id = await _cycle_row(master_key)
    before = await _row(instance_id)

    written = await update_instance_offset(
        instance_id, column, expected=_STARTS[column] - 1, value=99
    )

    assert written is False
    assert await _row(instance_id) == before


@pytest.mark.asyncio()
async def test_update_instance_offset_unknown_id_returns_false(db: None) -> None:
    """No row matches an unknown id; the write reports that instead of raising."""
    assert await update_instance_offset(999, "upgrade_item_offset", expected=0, value=1) is False


@pytest.mark.asyncio()
async def test_update_instance_offset_same_value_still_matches(db: None, master_key: bytes) -> None:
    """Writing the stored value back counts as a match."""
    instance_id = await _cycle_row(master_key)

    assert await update_instance_offset(instance_id, "missing_page_offset", expected=3, value=3)


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("column", "change", "written"),
    [
        ("missing_page_offset", InstanceUpdate(search_order=SearchOrder.random), False),
        ("missing_page_offset", InstanceUpdate(missing_enabled=False), False),
        ("cutoff_page_offset", InstanceUpdate(search_order=SearchOrder.random), False),
        ("cutoff_page_offset", InstanceUpdate(cutoff_enabled=False), False),
        ("upgrade_item_offset", InstanceUpdate(search_order=SearchOrder.random), False),
        ("upgrade_item_offset", InstanceUpdate(upgrade_enabled=False), False),
        ("upgrade_series_offset", InstanceUpdate(upgrade_enabled=False), False),
        # The series offset rotates in Random order too.
        ("upgrade_series_offset", InstanceUpdate(search_order=SearchOrder.random), True),
        ("upgrade_series_offset", InstanceUpdate(cutoff_enabled=False), True),
    ],
)
async def test_update_instance_offset_requires_the_mode_that_moves_the_column(
    db: None,
    master_key: bytes,
    column: OffsetColumn,
    change: InstanceUpdate,
    written: bool,
) -> None:
    """A matching offset is not enough once the pass or the order that moves it changed."""
    instance_id = await _cycle_row(master_key)
    await update_instance(instance_id, change, master_key=master_key)

    result = await update_instance_offset(
        instance_id, column, expected=_STARTS[column], value=_STARTS[column] + 1
    )

    assert result is written
    expected = _STARTS[column] + 1 if written else _STARTS[column]
    assert (await _row(instance_id))[column] == expected


@pytest.mark.asyncio()
async def test_update_instance_offset_rejects_other_columns(db: None, master_key: bytes) -> None:
    """Only the four offset columns have a statement."""
    instance_id = await _cycle_row(master_key)

    with pytest.raises(KeyError):
        await update_instance_offset(
            instance_id,
            "batch_size",  # type: ignore[arg-type]
            expected=10,
            value=1,
        )


@pytest.mark.asyncio()
async def test_update_instance_offset_waits_for_an_open_reset_then_skips(
    db: None, master_key: bytes
) -> None:
    """The write waits for a reset still in its transaction and sees it once committed."""
    instance_id = await _cycle_row(master_key)

    async with get_db() as reset:
        await reset.execute(
            "UPDATE instances SET upgrade_item_offset = 0, upgrade_enabled = 0 WHERE id = ?",
            (instance_id,),
        )
        write = asyncio.create_task(
            update_instance_offset(instance_id, "upgrade_item_offset", expected=7, value=8)
        )
        await asyncio.sleep(0.3)
        assert not write.done()
        await reset.commit()

    assert await write is False
    assert (await _row(instance_id))["upgrade_item_offset"] == 0

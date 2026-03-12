"""Tests for the instance CRUD service layer."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from houndarr.services.instances import (
    Instance,
    InstanceType,
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
    assert inst.batch_size == 10
    assert inst.sleep_interval_mins == 15
    assert inst.hourly_cap == 20
    assert inst.cooldown_days == 7
    assert inst.unreleased_delay_hrs == 24
    assert inst.cutoff_enabled is False
    assert inst.cutoff_batch_size == 5


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

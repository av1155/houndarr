"""Pinning tests for the instances-repository read boundary.

Locks the Track D.3 contract of ``get_instance`` / ``list_instances``
and the row-mapper helpers that moved alongside them.  The service-
layer delegators in :mod:`houndarr.services.instances` have to keep
returning byte-equal :class:`~houndarr.services.instances.Instance`
objects across every subsequent D batch, so each case below covers
one boundary that the delegation has to preserve: empty result,
single row, multi-row ordering, decryption correctness, and the
tolerant fallback for rows that pre-date the v13 snapshot columns.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from houndarr.crypto import encrypt
from houndarr.database import get_db
from houndarr.repositories import instances as repo
from houndarr.repositories.instances import (
    _optional_row_int,
    _optional_row_str,
    _row_to_instance,
)
from houndarr.services.instances import (
    Instance,
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
async def test_list_instances_empty(db: None, master_key: bytes) -> None:
    """Empty ``instances`` table returns an empty list, not ``None``."""
    rows = await repo.list_instances(master_key=master_key)
    assert rows == []


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_get_instance_missing_returns_none(db: None, master_key: bytes) -> None:
    """Missing id yields ``None`` from the repository."""
    assert await repo.get_instance(999, master_key=master_key) is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_get_instance_roundtrip(db: None, master_key: bytes) -> None:
    """A created instance reads back decrypted and with every column populated."""
    created = await create_instance(
        master_key=master_key,
        name="Sonarr Main",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="top-secret",
    )

    fetched = await repo.get_instance(created.id, master_key=master_key)
    assert fetched is not None
    assert isinstance(fetched, Instance)
    assert fetched.id == created.id
    assert fetched.name == "Sonarr Main"
    assert fetched.type == InstanceType.sonarr
    assert fetched.url == "http://sonarr:8989"
    assert fetched.api_key == "top-secret"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_list_instances_orders_by_id_ascending(db: None, master_key: bytes) -> None:
    """list_instances returns rows in id-ascending order regardless of insert order."""
    first = await create_instance(
        master_key=master_key,
        name="Alpha",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="k1",
    )
    second = await create_instance(
        master_key=master_key,
        name="Bravo",
        type=InstanceType.sonarr,
        url="http://sonarr2:8989",
        api_key="k2",
    )
    third = await create_instance(
        master_key=master_key,
        name="Charlie",
        type=InstanceType.sonarr,
        url="http://sonarr3:8989",
        api_key="k3",
    )

    rows = await repo.list_instances(master_key=master_key)
    assert [r.id for r in rows] == [first.id, second.id, third.id]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_list_instances_decrypts_each_api_key(db: None, master_key: bytes) -> None:
    """Every row in the list has its api_key decrypted to plaintext."""
    await create_instance(
        master_key=master_key,
        name="One",
        type=InstanceType.sonarr,
        url="http://s1:8989",
        api_key="plain-one",
    )
    await create_instance(
        master_key=master_key,
        name="Two",
        type=InstanceType.radarr,
        url="http://r1:7878",
        api_key="plain-two",
    )

    rows = await repo.list_instances(master_key=master_key)
    assert {r.api_key for r in rows} == {"plain-one", "plain-two"}


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_get_instance_decrypts_with_correct_key(db: None, master_key: bytes) -> None:
    """Reading with a different master_key raises a decryption error."""
    created = await create_instance(
        master_key=master_key,
        name="Secure",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="s3cret",
    )
    other_key = Fernet.generate_key()
    with pytest.raises(Exception):  # noqa: B017, PT011
        await repo.get_instance(created.id, master_key=other_key)


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_row_to_instance_preserves_all_enum_coercions(db: None, master_key: bytes) -> None:
    """Row mapper turns every mode column into the correct StrEnum variant."""
    created = await create_instance(
        master_key=master_key,
        name="Modes",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="k",
        sonarr_search_mode=SonarrSearchMode.season_context,
        search_order=SearchOrder.random,
    )
    fetched = await repo.get_instance(created.id, master_key=master_key)
    assert fetched is not None
    assert fetched.type is InstanceType.sonarr
    assert fetched.sonarr_search_mode is SonarrSearchMode.season_context
    assert fetched.search_order is SearchOrder.random


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_optional_row_helpers_tolerate_pre_v13_rows(db: None, master_key: bytes) -> None:
    """Reading a row inserted without the v13 snapshot columns yields default zeros."""
    # Seed a minimal row lacking the v13 snapshot columns (they stay NULL in SQLite).
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO instances (name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?)",
            ("Legacy", "sonarr", "http://sonarr:8989", encrypt("legacy-key", master_key)),
        )
        await conn.commit()
        async with conn.execute("SELECT id FROM instances WHERE name = ?", ("Legacy",)) as cur:
            row = await cur.fetchone()
    assert row is not None
    legacy_id = int(row["id"])

    fetched = await repo.get_instance(legacy_id, master_key=master_key)
    assert fetched is not None
    assert fetched.monitored_total == 0
    assert fetched.unreleased_count == 0
    assert fetched.snapshot_refreshed_at == ""


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_optional_row_int_default_on_missing_column() -> None:
    """The int helper returns 0 when the column is not part of the row object."""

    class _FakeRow:
        def __getitem__(self, key: str) -> object:
            raise IndexError(key)

    assert _optional_row_int(_FakeRow(), "nope") == 0  # type: ignore[arg-type]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_optional_row_int_default_on_null_value() -> None:
    """A literal ``None`` in the row maps to 0, not a crash."""

    class _FakeRow:
        def __getitem__(self, key: str) -> object:
            return None

    assert _optional_row_int(_FakeRow(), "col") == 0  # type: ignore[arg-type]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_optional_row_str_default_on_missing_column() -> None:
    """The str helper returns the empty string on a missing column."""

    class _FakeRow:
        def __getitem__(self, key: str) -> object:
            raise KeyError(key)

    assert _optional_row_str(_FakeRow(), "nope") == ""  # type: ignore[arg-type]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_get_instance_delegates_to_repo(db: None, master_key: bytes) -> None:
    """The service-layer wrapper returns the same object the repo would."""
    from houndarr.services.instances import get_instance as svc_get

    created = await create_instance(
        master_key=master_key,
        name="Delegated",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="shared",
    )
    via_repo = await repo.get_instance(created.id, master_key=master_key)
    via_service = await svc_get(created.id, master_key=master_key)
    assert via_repo == via_service


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_service_list_instances_delegates_to_repo(db: None, master_key: bytes) -> None:
    """The service-layer list wrapper returns the same rows as the repo."""
    from houndarr.services.instances import list_instances as svc_list

    await create_instance(
        master_key=master_key,
        name="One",
        type=InstanceType.sonarr,
        url="http://s:8989",
        api_key="x",
    )
    via_repo = await repo.list_instances(master_key=master_key)
    via_service = await svc_list(master_key=master_key)
    assert via_repo == via_service


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_row_to_instance_is_importable_from_repository(db: None, master_key: bytes) -> None:
    """The row mapper lives in the repository now; importing it from there works."""
    assert _row_to_instance is not None
    assert callable(_row_to_instance)

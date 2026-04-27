"""Pinning tests for the settings-repository SQL boundary.

Locks the Track D.2 contract of
:mod:`houndarr.repositories.settings` and the transitional
delegation from :mod:`houndarr.database`: every case below has to
stay byte-equal through later D batches so route callers and the
``database`` module can be migrated one file at a time without
regressing behaviour at the edges.
"""

from __future__ import annotations

import pytest

from houndarr.repositories import settings as repo


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_get_setting_returns_none_for_missing_key(db: None) -> None:
    """Missing keys yield ``None``; no implicit default in the repo API."""
    assert await repo.get_setting("does_not_exist") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_get_setting_returns_stored_value(db: None) -> None:
    """set_setting round-trips through get_setting for a simple value."""
    await repo.set_setting("alpha", "one")
    assert await repo.get_setting("alpha") == "one"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_set_setting_upserts_existing_key(db: None) -> None:
    """Second set overwrites the first via ON CONFLICT DO UPDATE."""
    await repo.set_setting("beta", "first")
    await repo.set_setting("beta", "second")
    assert await repo.get_setting("beta") == "second"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_set_setting_preserves_empty_string(db: None) -> None:
    """Empty string is stored verbatim and is distinct from a missing row."""
    await repo.set_setting("gamma", "")
    assert await repo.get_setting("gamma") == ""


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_set_setting_preserves_unicode(db: None) -> None:
    """Non-ASCII values round-trip byte-equal (TEXT column, UTF-8 on SQLite)."""
    value = "héllo ☃ world"
    await repo.set_setting("unicode_key", value)
    assert await repo.get_setting("unicode_key") == value


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_setting_removes_existing_key(db: None) -> None:
    """delete_setting erases the row so a subsequent read returns None."""
    await repo.set_setting("delta", "value")
    await repo.delete_setting("delta")
    assert await repo.get_setting("delta") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_setting_noop_on_missing_key(db: None) -> None:
    """delete_setting is idempotent: calling it on a missing key succeeds."""
    await repo.delete_setting("never_was_there")
    assert await repo.get_setting("never_was_there") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_setting_only_removes_named_key(db: None) -> None:
    """delete_setting does not touch unrelated rows."""
    await repo.set_setting("keep_me", "still_here")
    await repo.set_setting("drop_me", "gone")
    await repo.delete_setting("drop_me")
    assert await repo.get_setting("keep_me") == "still_here"
    assert await repo.get_setting("drop_me") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_database_get_setting_returns_default_on_missing_key(db: None) -> None:
    """Legacy wrapper preserves the ``default=`` behaviour its callers rely on."""
    from houndarr.database import get_setting as db_get

    assert await db_get("absent_key", default="fallback") == "fallback"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_database_get_setting_returns_none_without_default(db: None) -> None:
    """Legacy wrapper still returns ``None`` when no default is supplied."""
    from houndarr.database import get_setting as db_get

    assert await db_get("also_absent") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_database_get_setting_prefers_stored_value_over_default(db: None) -> None:
    """A stored value wins over the caller's default in the legacy path."""
    from houndarr.database import get_setting as db_get

    await repo.set_setting("real_key", "real_value")
    assert await db_get("real_key", default="fallback") == "real_value"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_database_set_setting_delegates_to_repo(db: None) -> None:
    """A write through the legacy wrapper is visible via the repository read."""
    from houndarr.database import set_setting as db_set

    await db_set("shared_key", "via_db_wrapper")
    assert await repo.get_setting("shared_key") == "via_db_wrapper"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_repo_set_setting_visible_to_database_wrapper(db: None) -> None:
    """A write through the repository is visible via the legacy wrapper read."""
    from houndarr.database import get_setting as db_get

    await repo.set_setting("shared_key_2", "via_repo")
    assert await db_get("shared_key_2") == "via_repo"

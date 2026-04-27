"""Pinning tests for the settings-repository SQL boundary.

Locks the contract of :mod:`houndarr.repositories.settings`: every
case below pins one boundary behaviour that route and service
callers rely on.
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
def test_database_no_longer_exposes_settings_shim() -> None:
    """``database.get_setting`` / ``set_setting`` are not re-exported.

    Callers import from :mod:`houndarr.repositories.settings`; if
    either name reappears on the database module a future
    contributor is re-introducing the indirection intentionally
    dropped, and this pin catches it.
    """
    import houndarr.database as _database_mod

    assert not hasattr(_database_mod, "get_setting")
    assert not hasattr(_database_mod, "set_setting")

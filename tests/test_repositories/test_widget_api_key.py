"""Tests for the widget API key repository."""

from __future__ import annotations

import pytest

from houndarr.auth.houndarr_api_key import hash_api_key
from houndarr.repositories import widget_api_key


@pytest.mark.asyncio()
async def test_get_returns_none_when_no_widget_key_exists(db: None) -> None:
    assert await widget_api_key.get() is None


@pytest.mark.asyncio()
async def test_set_inserts_and_replaces_single_widget_key(db: None) -> None:
    first_hash = hash_api_key("hndarr_first")
    second_hash = hash_api_key("hndarr_second")

    first = await widget_api_key.set(first_hash)
    assert first.hash == first_hash
    assert first.created_at
    assert first.last_used_at is None

    await widget_api_key.touch_last_used()
    touched = await widget_api_key.get()
    assert touched is not None
    assert touched.last_used_at is not None

    second = await widget_api_key.set(second_hash)
    assert second.hash == second_hash
    assert second.last_used_at is None


@pytest.mark.asyncio()
async def test_touch_last_used_noops_without_key(db: None) -> None:
    await widget_api_key.touch_last_used()
    assert await widget_api_key.get() is None


@pytest.mark.asyncio()
async def test_set_rejects_non_sha256_digest(db: None) -> None:
    with pytest.raises(ValueError):
        await widget_api_key.set("not-a-digest")


@pytest.mark.asyncio()
async def test_revoke_deletes_widget_key(db: None) -> None:
    await widget_api_key.set(hash_api_key("hndarr_revoke"))
    await widget_api_key.revoke()
    assert await widget_api_key.get() is None

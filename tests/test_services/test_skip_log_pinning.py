"""Characterisation tests for the skip-log sentinel in services.cooldown.

The existing ``tests/test_services/test_cooldown.py`` covers the
first-call, within-TTL, after-TTL, distinct-keys, full-cache, expiry,
warning, and concurrent-serialisation cases.  This module pins the
remaining boundary behaviour that a refactor could silently drift:

* ``_reset_skip_log_cache`` empties the cache and restores first-call
  semantics to any previously-seen key.
* The TTL comparison is strict (``now - entry < _SKIP_LOG_TTL_SECONDS``),
  so an entry at exactly the TTL age is treated as expired, and the front
  purge drops it.
* A cache hit changes neither the entry's position nor its timestamp,
  so the cache stays in write order and the TTL runs from the logged row.
* Rewriting an expired key moves it to the end, behind every live entry.
* A cache hit never drops a live entry, even when the cache holds more
  than the cap.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from houndarr.services import cooldown as cooldown_module
from houndarr.services.cooldown import (
    _reset_skip_log_cache,
    should_log_skip,
)

pytestmark = pytest.mark.pinning


@pytest.fixture(autouse=True)
def _reset_sentinel() -> Iterator[None]:
    """Clear the module-level cache between tests so order cannot leak."""
    _reset_skip_log_cache()
    yield
    _reset_skip_log_cache()


# _reset_skip_log_cache


class TestResetSkipLogCache:
    """Pin the reset helper's contract."""

    @pytest.mark.asyncio()
    async def test_reset_empties_cache(self) -> None:
        """After calls land, reset drops every entry."""
        key_a = (1, 101, "missing", "cooldown")
        key_b = (1, 102, "missing", "cooldown")
        await should_log_skip(key_a)
        await should_log_skip(key_b)
        assert len(cooldown_module._SKIP_LOG_CACHE) == 2

        _reset_skip_log_cache()

        assert len(cooldown_module._SKIP_LOG_CACHE) == 0

    @pytest.mark.asyncio()
    async def test_reset_restores_first_call_semantics(self) -> None:
        """A key that returned False because of a previous hit returns True again after reset."""
        key = (1, 101, "missing", "cooldown")
        assert await should_log_skip(key) is True
        assert await should_log_skip(key) is False

        _reset_skip_log_cache()

        assert await should_log_skip(key) is True


# TTL boundary


@pytest.fixture()
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> float:
    """Freeze the sentinel's clock so an entry's age can equal the TTL exactly."""
    now = 1_000_000.0
    monkeypatch.setattr(cooldown_module, "monotonic", lambda: now)
    return now


class TestSkipLogTtlBoundary:
    """Pin the strict less-than comparison used for TTL expiry."""

    @pytest.mark.asyncio()
    async def test_entry_exactly_at_ttl_is_expired(self, frozen_clock: float) -> None:
        """``now - entry < TTL`` is strict; an entry aged exactly TTL is expired."""
        key = (1, 101, "missing", "cooldown")
        ttl = cooldown_module._SKIP_LOG_TTL_SECONDS
        cooldown_module._SKIP_LOG_CACHE[key] = frozen_clock - ttl

        assert await should_log_skip(key) is True

    @pytest.mark.asyncio()
    async def test_front_entry_exactly_at_ttl_is_purged(self, frozen_clock: float) -> None:
        """Another key's arrival purges a front entry aged exactly TTL."""
        stale = (1, 101, "missing", "cooldown")
        ttl = cooldown_module._SKIP_LOG_TTL_SECONDS
        cooldown_module._SKIP_LOG_CACHE[stale] = frozen_clock - ttl

        assert await should_log_skip((1, 102, "missing", "cooldown")) is True
        assert stale not in cooldown_module._SKIP_LOG_CACHE

    @pytest.mark.asyncio()
    async def test_entry_well_inside_ttl_is_fresh(self, frozen_clock: float) -> None:
        """An entry one second short of the TTL still suppresses the repeat."""
        key = (1, 101, "missing", "cooldown")
        ttl = cooldown_module._SKIP_LOG_TTL_SECONDS
        cooldown_module._SKIP_LOG_CACHE[key] = frozen_clock - (ttl - 1)

        assert await should_log_skip(key) is False


# Write order


class TestSkipLogWriteOrder:
    """Pin the write order that lets expired entries be purged from the front."""

    @pytest.mark.asyncio()
    async def test_cache_hit_keeps_position_and_timestamp(self) -> None:
        """A suppressed repeat leaves the entry where it was, with its original time."""
        keys = [(1, i, "missing", "cooldown") for i in range(3)]
        for key in keys:
            assert await should_log_skip(key) is True
        written_at = cooldown_module._SKIP_LOG_CACHE[keys[0]]

        assert await should_log_skip(keys[0]) is False

        assert list(cooldown_module._SKIP_LOG_CACHE) == keys
        assert cooldown_module._SKIP_LOG_CACHE[keys[0]] == written_at

    @pytest.mark.asyncio()
    async def test_expired_rewrite_moves_to_end(self) -> None:
        """Logging an expired key again files it behind every live entry."""
        keys = [(1, i, "missing", "cooldown") for i in range(3)]
        for key in keys:
            assert await should_log_skip(key) is True
        cooldown_module._SKIP_LOG_CACHE[keys[1]] = cooldown_module.monotonic() - 25 * 3600

        assert await should_log_skip(keys[1]) is True

        assert list(cooldown_module._SKIP_LOG_CACHE) == [keys[0], keys[2], keys[1]]

    @pytest.mark.asyncio()
    async def test_read_only_hit_does_not_trigger_eviction(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cache hit never drops a live entry, even above the cap."""
        monkeypatch.setattr(cooldown_module, "_SKIP_LOG_MAX_ENTRIES", 2)

        keys = [(1, i, "missing", "cooldown") for i in range(2)]
        for key in keys:
            await should_log_skip(key)

        # Lower the cap below the current size; a pure cache-hit call must
        # still leave both entries in place.
        monkeypatch.setattr(cooldown_module, "_SKIP_LOG_MAX_ENTRIES", 1)

        # Hit one of the existing keys.  Cache still holds both.
        assert await should_log_skip(keys[0]) is False
        assert len(cooldown_module._SKIP_LOG_CACHE) == 2


# Reason-bucket key discrimination


class TestSkipLogReasonBuckets:
    """Pin that different reason_bucket values collide only when they match exactly."""

    @pytest.mark.asyncio()
    async def test_reason_bucket_case_sensitive(self) -> None:
        """ "cooldown" and "Cooldown" are distinct cache keys."""
        lower = (1, 101, "missing", "cooldown")
        upper = (1, 101, "missing", "Cooldown")

        assert await should_log_skip(lower) is True
        assert await should_log_skip(upper) is True  # distinct key
        assert await should_log_skip(lower) is False

    @pytest.mark.asyncio()
    async def test_reason_bucket_no_substring_collision(self) -> None:
        """ "cd" is not a prefix of "cutoff_cd" for the purposes of dedup."""
        short = (1, 101, "cutoff", "cd")
        long = (1, 101, "cutoff", "cutoff_cd")
        hot = (1, 101, "missing", "hot_retry")

        assert await should_log_skip(short) is True
        assert await should_log_skip(long) is True  # distinct
        assert await should_log_skip(hot) is True  # distinct

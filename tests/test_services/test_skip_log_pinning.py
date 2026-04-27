"""Characterisation tests for the skip-log LRU sentinel in services.cooldown.

The existing ``tests/test_services/test_cooldown.py`` covers the
first-call, within-TTL, after-TTL, distinct-keys, simple-eviction,
and concurrent-serialisation cases.  This module pins the remaining
boundary behaviour that a refactor could silently drift:

* ``_reset_skip_log_cache`` empties the cache and restores first-call
  semantics to any previously-seen key.
* The TTL comparison is strict (``now - entry < _SKIP_LOG_TTL``), so an
  entry at exactly the TTL age is treated as expired.
* ``move_to_end`` runs on both cache hits and cache misses, so an entry
  that keeps getting touched within TTL is never evicted by LRU pressure.
* A capped cache evicts the LEAST-RECENTLY-TOUCHED entry (not the
  least-recently-inserted) when a new entry lands.
* The LRU eviction only happens on insert; read-only hits never drop
  entries below the cap.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

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


class TestSkipLogTtlBoundary:
    """Pin the strict less-than comparison used for TTL expiry."""

    @pytest.mark.asyncio()
    async def test_entry_exactly_at_ttl_is_expired(self) -> None:
        """``now - entry < TTL`` is strict; an entry aged exactly TTL is expired."""
        key = (1, 101, "missing", "cooldown")
        # Seed with a timestamp that is *exactly* TTL in the past.
        cooldown_module._SKIP_LOG_CACHE[key] = datetime.now(UTC) - cooldown_module._SKIP_LOG_TTL

        # Strict less-than means the entry is NOT fresh; should_log_skip writes a new one.
        assert await should_log_skip(key) is True

    @pytest.mark.asyncio()
    async def test_entry_well_inside_ttl_is_fresh(self) -> None:
        """An entry comfortably inside the TTL window suppresses further writes.

        One second of headroom avoids a race with the subsequent call's
        clock advance; exact-microsecond tests are unstable against wall time.
        """
        key = (1, 101, "missing", "cooldown")
        almost_ttl = cooldown_module._SKIP_LOG_TTL - timedelta(seconds=1)
        cooldown_module._SKIP_LOG_CACHE[key] = datetime.now(UTC) - almost_ttl

        assert await should_log_skip(key) is False


# LRU ordering / move_to_end semantics


class TestSkipLogLruOrdering:
    """Pin LRU behaviour around move_to_end and eviction order."""

    @pytest.mark.asyncio()
    async def test_move_to_end_on_cache_hit_prevents_eviction(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A repeatedly-touched key survives pressure that evicts cold keys."""
        monkeypatch.setattr(cooldown_module, "_SKIP_LOG_MAX_ENTRIES", 3)

        keys = [(1, i, "missing", "cooldown") for i in range(4)]
        # Seed keys 0..2
        for key in keys[:3]:
            assert await should_log_skip(key) is True

        # Touch key[0] so it becomes the most-recently-used (its entry refreshes to 'now').
        assert await should_log_skip(keys[0]) is False

        # New insert pressure: keys[1] should be the cold victim, NOT keys[0].
        assert await should_log_skip(keys[3]) is True
        assert keys[0] in cooldown_module._SKIP_LOG_CACHE
        assert keys[1] not in cooldown_module._SKIP_LOG_CACHE
        assert keys[2] in cooldown_module._SKIP_LOG_CACHE
        assert keys[3] in cooldown_module._SKIP_LOG_CACHE

    @pytest.mark.asyncio()
    async def test_eviction_pops_from_front(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without any touches, evictions strip from the oldest-inserted end."""
        monkeypatch.setattr(cooldown_module, "_SKIP_LOG_MAX_ENTRIES", 2)

        keys = [(1, i, "missing", "cooldown") for i in range(3)]
        for key in keys:
            await should_log_skip(key)

        # Size capped at 2, oldest dropped.
        assert len(cooldown_module._SKIP_LOG_CACHE) == 2
        assert list(cooldown_module._SKIP_LOG_CACHE.keys()) == [keys[1], keys[2]]

    @pytest.mark.asyncio()
    async def test_read_only_hit_does_not_trigger_eviction(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cache hit touches move_to_end but does not re-check the cap."""
        monkeypatch.setattr(cooldown_module, "_SKIP_LOG_MAX_ENTRIES", 2)

        keys = [(1, i, "missing", "cooldown") for i in range(2)]
        for key in keys:
            await should_log_skip(key)

        # Lower the cap below current size by rewriting; a pure cache-hit call
        # must NOT trigger eviction (eviction only runs on the insert branch).
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

        assert await should_log_skip(short) is True
        assert await should_log_skip(long) is True  # distinct

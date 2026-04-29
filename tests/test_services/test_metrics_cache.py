"""Tests for the dashboard aggregate cache (issue #586).

The cache wraps the four slow ``search_log`` aggregations
(``gather_window_metrics``, ``gather_lifetime_metrics``,
``gather_active_errors``, ``gather_recent_searches``) with a 20-second
TTL and single-flight semantics so tens of dashboard tabs polling the
same envelope land on a single DB scan.  The tests below pin the
contract: a hit avoids the DB entirely; ``cache_clear`` forces a fresh
scan on the next call; live signals (cycle-end timestamps, cooldown
rows) bypass the cache so the next-patrol countdown stays accurate.
"""

from __future__ import annotations

import pytest

from houndarr.services.metrics import (
    DASHBOARD_CACHE_TTL_SECONDS,
    DashboardAggregates,
    build_aggregate_cache,
    invalidate_dashboard_cache,
)


def test_build_aggregate_cache_returns_none_when_ttl_zero() -> None:
    """``ttl_seconds=0`` opts the route into the uncached fallback path.

    The conftest ``_disable_dashboard_cache`` fixture relies on this:
    every legacy test runs with ``DASHBOARD_CACHE_TTL_SECONDS`` patched
    to 0, and the route handler's ``aggregate_cache is None`` branch
    falls through to a fresh DB scan.
    """
    cache = build_aggregate_cache(ttl_seconds=0)
    assert cache is None


def test_build_aggregate_cache_returns_callable_when_ttl_positive() -> None:
    """A non-zero TTL produces a callable with ``cache_clear``."""
    cache = build_aggregate_cache(ttl_seconds=5)
    assert cache is not None
    assert callable(cache)
    assert hasattr(cache, "cache_clear")


def test_default_ttl_matches_dashboard_polling_cadence() -> None:
    """The cache TTL stays in lockstep with the 30 s HTMX poll.

    Pinning the constant prevents an off-by-one tuning change from
    making the cache TTL longer than the poll, which would let a
    settings mutation hide behind one full poll cycle even after
    invalidation fires.
    """
    assert 5 < DASHBOARD_CACHE_TTL_SECONDS < 30


@pytest.mark.asyncio()
async def test_cache_hit_skips_db_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call within the TTL reuses the result without re-running.

    Asserts the alru_cache wrapper's hit path: the underlying
    ``_gather_dashboard_aggregates`` runs exactly once across two
    awaits with the same key.  The single-flight guarantee from
    async-lru protects the dashboard from thundering-herd polls.
    """
    import houndarr.services.metrics as metrics_module

    call_count = {"n": 0}

    async def _stub_gather(ids: tuple[int, ...]) -> DashboardAggregates:
        call_count["n"] += 1
        return DashboardAggregates()

    monkeypatch.setattr(metrics_module, "_gather_dashboard_aggregates", _stub_gather)

    cache = build_aggregate_cache(ttl_seconds=5)
    assert cache is not None

    await cache((1, 2))
    await cache((1, 2))

    assert call_count["n"] == 1


@pytest.mark.asyncio()
async def test_cache_clear_forces_fresh_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cache_clear`` invalidates every entry, the next call hits the DB.

    Every mutation route fans out to ``invalidate_dashboard_cache``
    which calls ``cache_clear``; this is the only invalidation path
    the production code uses.  A regression that swaps clearing for a
    no-op would let stale data linger on the dashboard after the
    operator created, edited, or deleted an instance.
    """
    import houndarr.services.metrics as metrics_module

    call_count = {"n": 0}

    async def _stub_gather(ids: tuple[int, ...]) -> DashboardAggregates:
        call_count["n"] += 1
        return DashboardAggregates()

    monkeypatch.setattr(metrics_module, "_gather_dashboard_aggregates", _stub_gather)

    cache = build_aggregate_cache(ttl_seconds=5)
    assert cache is not None

    await cache((1, 2))
    cache.cache_clear()
    await cache((1, 2))

    assert call_count["n"] == 2


def test_invalidate_dashboard_cache_no_op_when_attribute_missing() -> None:
    """The helper is safe to call when the cache hasn't been built.

    Tests that bypass the lifespan (sync-only tests, isolated unit
    tests) leave ``app.state.aggregate_cache`` unset; the helper
    still has to be callable from mutation routes that could be
    exercised without a full app boot.
    """

    class _BareState:
        pass

    invalidate_dashboard_cache(_BareState())


def test_invalidate_dashboard_cache_calls_clear_when_present() -> None:
    """When a cache is attached, the helper invokes ``cache_clear``."""

    cleared = {"called": False}

    class _FakeCache:
        def cache_clear(self) -> None:
            cleared["called"] = True

    class _State:
        aggregate_cache: _FakeCache = _FakeCache()

    invalidate_dashboard_cache(_State())
    assert cleared["called"] is True

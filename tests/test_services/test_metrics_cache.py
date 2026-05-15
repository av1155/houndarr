"""Tests for the dashboard aggregate cache (issue #586).

The cache wraps the slow ``search_log`` aggregations
(``gather_window_metrics``, ``gather_lifetime_metrics``,
``gather_active_errors``, ``gather_recent_searches``, 7-day search count) with a 20-second
TTL and single-flight semantics so tens of dashboard tabs polling the
same envelope land on a single DB scan.  The tests below pin the
contract: a hit avoids the DB entirely; ``cache_clear`` forces a fresh
scan on the next call; live signals (cycle-end timestamps, cooldown
rows) bypass the cache so the next-patrol countdown stays accurate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from houndarr.database import get_db
from houndarr.services.metrics import (
    DASHBOARD_CACHE_TTL_SECONDS,
    DashboardAggregates,
    build_aggregate_cache,
    gather_cached_searches_7d,
    gather_searches_7d,
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


@pytest.mark.asyncio()
async def test_gather_searches_7d_counts_recent_searched_rows(db: None) -> None:
    """The widget count excludes old rows and non-search actions."""
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO search_log (action, timestamp) VALUES (?, datetime('now', ?))",
            [
                ("searched", "-1 day"),
                ("searched", "-8 days"),
                ("skipped", "-1 day"),
            ],
        )
        await conn.commit()
        assert await gather_searches_7d(conn) == 1


@pytest.mark.asyncio()
async def test_gather_searches_7d_uses_production_timestamp_format(db: None) -> None:
    """The 7-day cutoff should compare against ISO timestamps written by Houndarr."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    before_cutoff = (cutoff - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    after_cutoff = (cutoff + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO search_log (action, timestamp) VALUES ('searched', ?)",
            [(before_cutoff,), (after_cutoff,)],
        )
        await conn.commit()
        assert await gather_searches_7d(conn) == 1


@pytest.mark.asyncio()
async def test_gather_cached_searches_7d_reuses_aggregate_cache(db: None) -> None:
    """Widget polling should read the cached 7-day count when the cache is active."""

    class _FakeCache:
        def __init__(self) -> None:
            self.ids: tuple[int, ...] | None = None

        async def __call__(self, instance_ids_tuple: tuple[int, ...]) -> DashboardAggregates:
            self.ids = instance_ids_tuple
            return DashboardAggregates(searches_7d=44)

        def cache_clear(self) -> None:
            pass

    cache = _FakeCache()
    async with get_db() as conn:
        count = await gather_cached_searches_7d(
            conn,
            instance_ids=[2, 1],
            aggregate_cache=cache,
        )

    assert count == 44
    assert cache.ids == (1, 2)


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

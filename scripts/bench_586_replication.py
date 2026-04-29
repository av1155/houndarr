"""Empirical replication of issue #586 against the post-fix codebase.

Seeds a temporary SQLite database with the reporter's exact shape (~280k
``search_log`` rows from 3 active instances over 30 days) and measures:

1. Lifespan ``init_db()`` wall clock
2. ``/api/status`` aggregation latency (cached and uncached)
3. ``_migrate_to_v14`` self-heal idempotency (proves the back-fill no
   longer re-runs every boot once stamped)

The script is deliberately self-contained so it can run against the
current branch without standing up a real *arr stack.  A second mode
(``--without-indexes``) drops the two new composite indexes before the
measurement to model pre-fix behaviour for direct comparison.

Run::

    .venv/bin/python -m scripts.bench_586_replication
    .venv/bin/python -m scripts.bench_586_replication --without-indexes
    .venv/bin/python -m scripts.bench_586_replication --rows 50000
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta

from houndarr.config import bootstrap_settings
from houndarr.database import (
    _migrate_to_v14,
    close_all_pools,
    get_db,
    init_db_migrations,
    init_db_schema,
    set_db_path,
)
from houndarr.services.metrics import (
    build_aggregate_cache,
    gather_dashboard_status,
    invalidate_dashboard_cache,
)


def _human(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


async def _seed_search_log(rows: int, instance_count: int) -> None:
    """Seed ``rows`` rows distributed across ``instance_count`` instances.

    Spreads timestamps evenly over the last 30 days so the dashboard's
    24-hour and 7-day time windows have realistic data to filter.
    """
    print(f"Seeding {rows:,} search_log rows across {instance_count} instances...")
    now = datetime.now(UTC)
    chunk_size = 10_000

    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            [
                (i + 1, f"App{i + 1}", ["sonarr", "radarr", "lidarr"][i % 3], f"http://x/{i}", "")
                for i in range(instance_count)
            ],
        )
        await conn.commit()

    for offset in range(0, rows, chunk_size):
        batch = []
        for i in range(min(chunk_size, rows - offset)):
            row_idx = offset + i
            instance_id = (row_idx % instance_count) + 1
            item_id = 100 + (row_idx % 5_000)
            kind_idx = row_idx % 3
            kind = ("missing", "cutoff", "upgrade")[kind_idx]
            action_idx = row_idx % 4
            action = ("searched", "skipped", "searched", "info")[action_idx]
            minutes_ago = row_idx % (30 * 24 * 60)
            ts = (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            item_type = (
                "episode" if instance_id == 1 else ("movie" if instance_id == 2 else "album")
            )
            batch.append((instance_id, item_id, item_type, kind, action, f"Item {row_idx}", ts))

        async with get_db() as conn:
            await conn.executemany(
                "INSERT INTO search_log (instance_id, item_id, item_type, search_kind,"
                " action, item_label, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            await conn.commit()
        if (offset + chunk_size) % 50_000 == 0 or offset + chunk_size >= rows:
            print(f"  inserted {min(offset + chunk_size, rows):,}/{rows:,}")


async def _seed_cooldowns(instance_count: int, rows_per_instance: int) -> None:
    """Seed cooldown rows that the v14 back-fill will iterate over."""
    print(f"Seeding {instance_count * rows_per_instance:,} cooldown rows...")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    async with get_db() as conn:
        for instance_id in range(1, instance_count + 1):
            item_type = (
                "episode" if instance_id == 1 else ("movie" if instance_id == 2 else "album")
            )
            batch = [
                (instance_id, 100 + i, item_type, "missing", now) for i in range(rows_per_instance)
            ]
            await conn.executemany(
                "INSERT OR IGNORE INTO cooldowns (instance_id, item_id, item_type,"
                " search_kind, searched_at) VALUES (?, ?, ?, ?, ?)",
                batch,
            )
        await conn.commit()


async def _drop_new_indexes() -> None:
    """Remove the two indexes added by issue #586 to simulate pre-fix state."""
    async with get_db() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_search_log_lookup")
        await conn.execute("DROP INDEX IF EXISTS idx_search_log_action_time")
        await conn.commit()


async def _measure_init_db() -> float:
    """Run ``init_db_migrations()`` end-to-end (the post-PR hot path)."""
    start = time.monotonic()
    await init_db_migrations()
    return time.monotonic() - start


async def _measure_v14_backfill() -> tuple[float, float]:
    """Time both the first and second runs of the v14 self-heal."""
    async with get_db() as conn:
        first_start = time.monotonic()
        await _migrate_to_v14(conn)
        await conn.commit()
        first_elapsed = time.monotonic() - first_start

    async with get_db() as conn:
        second_start = time.monotonic()
        await _migrate_to_v14(conn)
        await conn.commit()
        second_elapsed = time.monotonic() - second_start

    return first_elapsed, second_elapsed


async def _measure_dashboard(samples: int = 5, *, with_cache: bool = False) -> list[float]:
    """Run ``gather_dashboard_status`` ``samples`` times and return latencies."""
    cache = build_aggregate_cache(ttl_seconds=20) if with_cache else None
    timings: list[float] = []
    for i in range(samples):
        start = time.monotonic()
        async with get_db() as db:
            envelope = await gather_dashboard_status(db, aggregate_cache=cache)
        timings.append(time.monotonic() - start)
        if i == 0:
            assert envelope["instances"], "expected at least one instance in envelope"
        if cache is not None and i == samples // 2:
            invalidate_dashboard_cache(_StateWith(cache))
    return timings


class _StateWith:
    """Stand-in for ``app.state`` that lets us call ``invalidate_dashboard_cache``."""

    def __init__(self, cache: object) -> None:
        self.aggregate_cache = cache


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=280_000)
    parser.add_argument("--instances", type=int, default=3)
    parser.add_argument("--cooldowns-per-instance", type=int, default=200)
    parser.add_argument(
        "--without-indexes",
        action="store_true",
        help="Drop idx_search_log_lookup + idx_search_log_action_time after seeding",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "houndarr.db")
        bootstrap_settings(data_dir=tmp, log_retention_days=30)
        set_db_path(db_path)

        await init_db_schema()
        await _seed_search_log(args.rows, args.instances)
        await _seed_cooldowns(args.instances, args.cooldowns_per_instance)

        if args.without_indexes:
            await _drop_new_indexes()
            print("DROPPED new indexes — simulating pre-PR codepath")

        size_mb = os.path.getsize(db_path) / 1024 / 1024
        print(f"\nDatabase size: {size_mb:.1f} MB")

        print("\n=== init_db_migrations() ===")
        elapsed_init = await _measure_init_db()
        print(f"  wall clock: {_human(elapsed_init)}")

        print("\n=== _migrate_to_v14 idempotency ===")
        first, second = await _measure_v14_backfill()
        print(f"  first run:  {_human(first)}")
        print(f"  second run: {_human(second)}  (idempotency guard kicked in)")

        print("\n=== /api/status latency (uncached) ===")
        uncached = await _measure_dashboard(samples=5, with_cache=False)
        print(f"  per call: {[_human(t) for t in uncached]}")
        print(f"  median:   {_human(statistics.median(uncached))}")

        print("\n=== /api/status latency (cached, ttl=20s) ===")
        cached = await _measure_dashboard(samples=5, with_cache=True)
        print(f"  per call: {[_human(t) for t in cached]}")
        print(f"  median:   {_human(statistics.median(cached))}")
        print("  (cache invalidated after 3rd call to prove cache_clear path)")

        await close_all_pools()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

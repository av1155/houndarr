"""Isolate the v14 cooldown back-fill UPDATE cost with and without the index.

The post-fix ``_migrate_to_v14`` creates ``idx_search_log_lookup`` before
running the correlated UPDATE.  This benchmark compares the raw UPDATE
runtime in three scenarios at the reporter's reported scale:

1. WITH ``idx_search_log_lookup`` present (post-fix path).
2. WITHOUT the index (pre-fix path, what v1.10.0 shipped).
3. Pre-fix UPDATE plus the index built on demand (the manual workaround
   the reporter applied to survive startup).

Run::

    .venv/bin/python -m scripts.bench_586_v14_update_only --rows 280000
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta

from houndarr.database import (
    close_all_pools,
    get_db,
    init_db_schema,
    set_db_path,
)

_BACKFILL_SQL = """
UPDATE cooldowns
   SET search_kind = (
         SELECT sl.search_kind
           FROM search_log sl
          WHERE sl.instance_id = cooldowns.instance_id
            AND sl.item_id     = cooldowns.item_id
            AND sl.item_type   = cooldowns.item_type
            AND sl.action      = 'searched'
            AND sl.search_kind IN ('missing', 'cutoff', 'upgrade')
          ORDER BY sl.timestamp DESC
          LIMIT 1
       )
 WHERE EXISTS (
         SELECT 1 FROM search_log sl2
          WHERE sl2.instance_id = cooldowns.instance_id
            AND sl2.item_id     = cooldowns.item_id
            AND sl2.item_type   = cooldowns.item_type
            AND sl2.action      = 'searched'
            AND sl2.search_kind IN ('missing', 'cutoff', 'upgrade')
       )
"""


def _human(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


async def _seed(rows: int, instance_count: int, cooldowns_per_instance: int) -> None:
    print(f"Seeding {rows:,} log rows + {instance_count * cooldowns_per_instance:,} cooldowns...")
    now = datetime.now(UTC)
    chunk = 10_000
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            [
                (i + 1, f"App{i + 1}", ["sonarr", "radarr", "lidarr"][i % 3], f"http://x/{i}", "")
                for i in range(instance_count)
            ],
        )
        await conn.commit()
    for offset in range(0, rows, chunk):
        batch = []
        for i in range(min(chunk, rows - offset)):
            row_idx = offset + i
            instance_id = (row_idx % instance_count) + 1
            item_id = 100 + (row_idx % 5_000)
            kind = ("missing", "cutoff", "upgrade")[row_idx % 3]
            ts = (now - timedelta(minutes=row_idx % (30 * 24 * 60))).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
            item_type = ("episode", "movie", "album")[(instance_id - 1) % 3]
            batch.append((instance_id, item_id, item_type, kind, "searched", f"Item {row_idx}", ts))
        async with get_db() as conn:
            await conn.executemany(
                "INSERT INTO search_log (instance_id, item_id, item_type, search_kind,"
                " action, item_label, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            await conn.commit()
    async with get_db() as conn:
        for instance_id in range(1, instance_count + 1):
            item_type = ("episode", "movie", "album")[(instance_id - 1) % 3]
            cool_batch = [
                (instance_id, 100 + i, item_type, "missing", now.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
                for i in range(cooldowns_per_instance)
            ]
            await conn.executemany(
                "INSERT OR IGNORE INTO cooldowns (instance_id, item_id, item_type,"
                " search_kind, searched_at) VALUES (?, ?, ?, ?, ?)",
                cool_batch,
            )
        await conn.commit()


async def _drop_lookup_index() -> None:
    async with get_db() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_search_log_lookup")
        await conn.commit()


async def _create_lookup_index() -> None:
    async with get_db() as conn:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_log_lookup "
            "ON search_log(instance_id, item_id, item_type, timestamp DESC)"
        )
        await conn.commit()


async def _reset_cooldowns_to_default(instance_count: int, per_instance: int) -> None:
    """Reset cooldowns.search_kind to 'missing' so the UPDATE has work to do."""
    async with get_db() as conn:
        await conn.execute("UPDATE cooldowns SET search_kind = 'missing'")
        await conn.commit()


async def _time_update() -> float:
    async with get_db() as conn:
        start = time.monotonic()
        await conn.execute(_BACKFILL_SQL)
        await conn.commit()
        return time.monotonic() - start


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=280_000)
    parser.add_argument("--instances", type=int, default=3)
    parser.add_argument("--cooldowns-per-instance", type=int, default=200)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "houndarr.db")
        set_db_path(db_path)
        await init_db_schema()
        await _seed(args.rows, args.instances, args.cooldowns_per_instance)

        print("\n" + "=" * 60)
        print("SCENARIO 1: WITH idx_search_log_lookup (post-fix)")
        print("=" * 60)
        await _create_lookup_index()
        await _reset_cooldowns_to_default(args.instances, args.cooldowns_per_instance)
        elapsed_with = await _time_update()
        print(f"  v14 UPDATE: {_human(elapsed_with)}")

        print("\n" + "=" * 60)
        print("SCENARIO 2: WITHOUT idx_search_log_lookup (pre-fix v1.10.0)")
        print("=" * 60)
        await _drop_lookup_index()
        await _reset_cooldowns_to_default(args.instances, args.cooldowns_per_instance)
        elapsed_without = await _time_update()
        print(f"  v14 UPDATE: {_human(elapsed_without)}")

        print("\n" + "=" * 60)
        print("SCENARIO 3: Reporter's manual workaround (build index, then UPDATE)")
        print("=" * 60)
        await _drop_lookup_index()
        await _reset_cooldowns_to_default(args.instances, args.cooldowns_per_instance)
        idx_start = time.monotonic()
        await _create_lookup_index()
        idx_elapsed = time.monotonic() - idx_start
        elapsed_with_built = await _time_update()
        total = idx_elapsed + elapsed_with_built
        print(f"  CREATE INDEX: {_human(idx_elapsed)}")
        print(f"  v14 UPDATE:   {_human(elapsed_with_built)}")
        print(f"  total:        {_human(total)}")

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        speedup = elapsed_without / elapsed_with if elapsed_with > 0 else float("inf")
        print(f"  Pre-fix UPDATE:  {_human(elapsed_without)}")
        print(f"  Post-fix UPDATE: {_human(elapsed_with)}")
        print(f"  Speedup:         {speedup:.1f}x")

        await close_all_pools()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

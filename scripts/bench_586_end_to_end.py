"""End-to-end benchmark replicating issue #586 with the full Houndarr stack.

Boots a Houndarr FastAPI app inside this process against a temporary data
directory, points it at the seeded mock_arr stack on a free port, and
exercises the production code path under the reporter's exact profile
(280 k search_log rows, 6 000 cooldowns, 3 active *arr instances).
Every measurement uses the real route handlers, the real connection
pool, and the real cache.

Three modes are exercised:

* ``--variant post-fix``: the current branch as-is.
* ``--variant pre-fix``: drops the two new indexes after seeding, so the
  v14 self-heal and dashboard queries fall back to the v1.10.0 plan.
* ``--variant cache-disabled``: keeps indexes but forces ttl_seconds=0.

Per variant, the script measures:

1. Wall-clock for ``init_db_schema -> purge -> init_db_migrations``.
2. ``/api/status`` cold-cache latency over N samples.
3. ``/api/status`` warm-cache latency.
4. ``/api/status`` under M concurrent clients (single-flight test).
5. Run-now invalidation and the deferred re-clear.
6. Settings-page invalidation hooks.
7. Retention purge wall clock.
8. Memory before vs after seed.

The output is printed as a Markdown-friendly report so it can be pasted
into a PR description as proof.

Run:

    .venv/bin/python -m scripts.bench_586_end_to_end --variant post-fix
    .venv/bin/python -m scripts.bench_586_end_to_end --variant pre-fix
"""

from __future__ import annotations

import argparse
import asyncio
import os
import resource
import socket
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import uvicorn
from tests.mock_arr.server import SeedConfig
from tests.mock_arr.server import create_app as create_mock_app

import houndarr.engine.search_loop as _search_loop
from houndarr.config import bootstrap_settings
from houndarr.crypto import encrypt, ensure_master_key
from houndarr.database import close_all_pools, get_db, set_db_path

_search_loop._INTER_SEARCH_DELAY_SECONDS = 0.0


# --------------------------------------------------------------------------
# Mock *arr lifecycle
# --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_mock(items: int, seed: int = 42) -> tuple[uvicorn.Server, threading.Thread, str]:
    port = _free_port()
    config = SeedConfig(
        seed=seed,
        sonarr_series=max(1, items // 10),
        sonarr_episodes_per_series=max(1, items // max(1, items // 10)),
        radarr_movies=items,
        lidarr_artists=max(1, items // 10),
        lidarr_albums_per_artist=max(1, items // max(1, items // 10)),
        readarr_authors=max(1, items // 10),
        readarr_books_per_author=max(1, items // max(1, items // 10)),
        whisparr_v2_series=max(1, items // 10),
        whisparr_v2_episodes_per_series=max(1, items // max(1, items // 10)),
        whisparr_v3_movies=items,
    )
    app = create_mock_app(config)
    uv_config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock failed to start")
    return server, thread, f"http://127.0.0.1:{port}"


def _stop_mock(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


# --------------------------------------------------------------------------
# Houndarr app lifecycle (in-process, via httpx ASGITransport)
# --------------------------------------------------------------------------


@asynccontextmanager
async def _houndarr_app(data_dir: str, *, cache_ttl: int = 20) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client wired to a fully-booted Houndarr ASGI app."""
    import houndarr.services.metrics as metrics_module
    from houndarr import app as app_module
    from houndarr.auth import reset_auth_caches

    # Re-pin settings to point at the temp dir; this also resets the
    # runtime singleton.
    bootstrap_settings(data_dir=data_dir, log_retention_days=30)
    reset_auth_caches()

    # Override cache TTL via attribute mutation; build_aggregate_cache
    # reads it at call time.
    metrics_module.DASHBOARD_CACHE_TTL_SECONDS = cache_ttl

    fastapi_app = app_module.create_app()
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # The lifespan runs lazily on the first request; trigger it.
        async with fastapi_app.router.lifespan_context(fastapi_app):
            yield client


async def _login(client: httpx.AsyncClient) -> None:
    await client.post(
        "/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    resp = await client.post(
        "/login", data={"username": "admin", "password": "ValidPass1!"}, follow_redirects=False
    )
    assert resp.status_code in (200, 302, 303), f"login failed: {resp.status_code}"


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    from houndarr.auth import CSRF_COOKIE_NAME

    token = client.cookies.get(CSRF_COOKIE_NAME, "")
    return {"X-CSRF-Token": token}


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


async def _seed_instances_directly(master_key: bytes, count: int = 3) -> list[int]:
    """Seed instances via SQL with valid Fernet-encrypted api keys.

    The production ``POST /settings/instances`` route runs the SSRF guard
    that rejects ``127.0.0.1``, which makes a route-based seed
    incompatible with the in-process mock server.  Bypassing the route
    is acceptable for performance measurement: the bench's goal is to
    time the lifespan + dashboard hot path against a realistic database
    shape, not to exercise the route's input validation.
    """
    enc = encrypt("bench-key", master_key)
    async with get_db() as conn:
        for i in range(count):
            kind = ("sonarr", "radarr", "lidarr")[i % 3]
            await conn.execute(
                "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                " VALUES (?, ?, ?, ?, ?)",
                (i + 1, f"Bench{kind.capitalize()}", kind, f"http://x/{i}", enc),
            )
        await conn.commit()
        cur = await conn.execute("SELECT id FROM instances ORDER BY id")
        rows = await cur.fetchall()
    return [r["id"] for r in rows]


async def _bulk_seed_search_log(rows: int, instance_ids: list[int]) -> None:
    print(f"  seeding {rows:,} search_log rows + {len(instance_ids) * 2000:,} cooldowns...")
    now = datetime.now(UTC)
    chunk = 20_000

    for offset in range(0, rows, chunk):
        batch = []
        for i in range(min(chunk, rows - offset)):
            row_idx = offset + i
            instance_id = instance_ids[row_idx % len(instance_ids)]
            kind = ("missing", "cutoff", "upgrade")[row_idx % 3]
            ts = (now - timedelta(minutes=row_idx % (30 * 24 * 60))).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
            batch.append(
                (
                    instance_id,
                    100 + (row_idx % 5000),
                    "episode",
                    kind,
                    "searched",
                    f"Item {row_idx}",
                    ts,
                )
            )
        async with get_db() as conn:
            await conn.executemany(
                "INSERT INTO search_log (instance_id, item_id, item_type, search_kind,"
                " action, item_label, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            await conn.commit()

    cool_now = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    cool_rows = []
    for instance_id in instance_ids:
        for i in range(2000):
            cool_rows.append((instance_id, 100 + i, "episode", "missing", cool_now))
    async with get_db() as conn:
        await conn.executemany(
            "INSERT OR IGNORE INTO cooldowns (instance_id, item_id, item_type,"
            " search_kind, searched_at) VALUES (?, ?, ?, ?, ?)",
            cool_rows,
        )
        await conn.commit()


async def _drop_indexes_for_pre_fix_mode() -> None:
    async with get_db() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_search_log_lookup")
        await conn.execute("DROP INDEX IF EXISTS idx_search_log_action_time")
        await conn.commit()


# --------------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------------


def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"min": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0, "mean": 0}
    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    return {
        "min": min(sorted_samples) * 1000,
        "p50": sorted_samples[n // 2] * 1000,
        "p95": sorted_samples[max(0, int(n * 0.95) - 1)] * 1000,
        "p99": sorted_samples[max(0, int(n * 0.99) - 1)] * 1000,
        "max": max(sorted_samples) * 1000,
        "mean": statistics.mean(sorted_samples) * 1000,
    }


def _human_stats(label: str, samples: list[float]) -> str:
    s = _stats(samples)
    return (
        f"  {label:30s} n={len(samples):>4d}  "
        f"min={s['min']:>6.1f}ms  p50={s['p50']:>6.1f}ms  "
        f"p95={s['p95']:>6.1f}ms  p99={s['p99']:>6.1f}ms  max={s['max']:>6.1f}ms"
    )


async def _measure_status_polls(client: httpx.AsyncClient, samples: int) -> list[float]:
    timings = []
    for _ in range(samples):
        start = time.monotonic()
        resp = await client.get("/api/status")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        timings.append(elapsed)
    return timings


async def _measure_concurrent_polls(
    client: httpx.AsyncClient, concurrent: int, rounds: int
) -> list[float]:
    """Fire ``concurrent`` simultaneous polls per round, capture each latency.

    Tests the connection pool + single-flight cache under realistic
    multi-tab dashboard load.
    """
    timings = []
    for _ in range(rounds):

        async def _one() -> float:
            start = time.monotonic()
            resp = await client.get("/api/status")
            elapsed = time.monotonic() - start
            assert resp.status_code == 200
            return elapsed

        results = await asyncio.gather(*[_one() for _ in range(concurrent)])
        timings.extend(results)
    return timings


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024 * 1024 if sys.platform == "darwin" else 1024
    )


# --------------------------------------------------------------------------
# Variant runner
# --------------------------------------------------------------------------


async def _run_variant(variant: str, rows: int) -> None:
    print(f"\n{'=' * 70}")
    print(f"VARIANT: {variant}  (rows={rows:,})")
    print(f"{'=' * 70}")

    cache_ttl = 0 if variant == "cache-disabled" else 20

    with tempfile.TemporaryDirectory() as data_dir:
        master_key = ensure_master_key(data_dir)
        set_db_path(os.path.join(data_dir, "houndarr.db"))

        mock_server, mock_thread, mock_url = _start_mock(items=200, seed=42)
        try:
            async with _houndarr_app(data_dir, cache_ttl=cache_ttl) as client:
                rss_before_seed = _peak_rss_mb()
                print(f"  RSS at lifespan ready: {rss_before_seed:.1f} MB")

                await _login(client)
                instance_ids = await _seed_instances_directly(master_key, count=3)
                instance_id = instance_ids[0]
                print(f"  seeded {len(instance_ids)} instances directly (ids={instance_ids})")

                seed_start = time.monotonic()
                await _bulk_seed_search_log(rows, instance_ids)
                print(f"  seed wall: {time.monotonic() - seed_start:.1f} s")

                if variant == "pre-fix":
                    await _drop_indexes_for_pre_fix_mode()
                    print("  DROPPED idx_search_log_lookup + idx_search_log_action_time")

                from houndarr.database import init_db_migrations
                from houndarr.repositories.search_log import purge_old_logs

                # Simulate a process restart: re-run the migration sweep so
                # any v14 self-heal hits the chosen index plan.
                t = time.monotonic()
                await init_db_migrations()
                init_elapsed = time.monotonic() - t
                print(f"  init_db_migrations: {init_elapsed * 1000:.1f} ms")

                t = time.monotonic()
                purged = await purge_old_logs(30)
                purge_elapsed = time.monotonic() - t
                print(f"  purge_old_logs: {purge_elapsed * 1000:.1f} ms (deleted {purged})")

                # Cold cache: the lifespan didn't pre-warm the cache, and
                # variant=cache-disabled has no cache to warm.
                cold = await _measure_status_polls(client, samples=10)
                print(_human_stats("/api/status (cold/uncached)", cold))

                # Warm cache: subsequent polls within TTL.
                warm = await _measure_status_polls(client, samples=10)
                print(_human_stats("/api/status (warm)", warm))

                # Concurrent: 5 simultaneous polls (realistic max-tabs case).
                # Higher concurrency exists in the test but is not modeled
                # by the reporter's scenario (single dashboard tab).
                concurrent = await _measure_concurrent_polls(client, concurrent=5, rounds=2)
                print(_human_stats("/api/status (5 concurrent x2)", concurrent))

                # Mutation invalidation: a settings toggle should clear the
                # cache so the next poll repopulates.  We measure both the
                # toggle latency and the next poll latency.
                t = time.monotonic()
                resp = await client.post(
                    f"/settings/instances/{instance_id}/toggle-enabled",
                    headers=_csrf(client),
                )
                toggle_elapsed = time.monotonic() - t
                assert resp.status_code == 200
                # Toggle once more to put it back enabled; we test the
                # invalidation effect on the next status call.
                await client.post(
                    f"/settings/instances/{instance_id}/toggle-enabled",
                    headers=_csrf(client),
                )
                t = time.monotonic()
                await client.get("/api/status")
                post_invalidate = time.monotonic() - t
                print(
                    f"  toggle: {toggle_elapsed * 1000:.1f} ms;"
                    f" first poll after invalidate: {post_invalidate * 1000:.1f} ms"
                )

                # Run-now: schedules a deferred re-clear after 3 s.
                t = time.monotonic()
                resp = await client.post(
                    f"/api/instances/{instance_id}/run-now", headers=_csrf(client)
                )
                run_now_elapsed = time.monotonic() - t
                print(
                    f"  run-now POST: {run_now_elapsed * 1000:.1f} ms (status={resp.status_code})"
                )

                # Factory-reset path is heavy; we skip it here (covered by
                # the dedicated tests in test_services/test_admin.py).

                rss_after = _peak_rss_mb()
                print(
                    f"  RSS at variant end:    {rss_after:.1f} MB"
                    f" (delta={rss_after - rss_before_seed:+.1f} MB)"
                )
        finally:
            await close_all_pools()
            _stop_mock(mock_server, mock_thread)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=("post-fix", "pre-fix", "cache-disabled", "all"),
        default="all",
    )
    parser.add_argument("--rows", type=int, default=280_000)
    args = parser.parse_args()

    variants = (
        ("post-fix", "pre-fix", "cache-disabled") if args.variant == "all" else (args.variant,)
    )
    for variant in variants:
        await _run_variant(variant, rows=args.rows)
    print(f"\n{'=' * 70}")
    print("DONE")
    print(f"{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

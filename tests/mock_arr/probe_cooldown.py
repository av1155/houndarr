"""Cooldown distribution probe.

Drives the engine for many cycles without wiping the cooldown table and
measures, for each item in the backlog, the distribution of times it
gets searched. The promise being verified: cooldown plus the random
search algorithm together produce roughly even attention across items
once steady state is reached, with no item systematically stuck waiting
indefinitely.

Method:

1. Monkey-patch ``houndarr.repositories.cooldowns._now_utc`` so each
   cycle advances simulated time by ``cooldown_days + 1 hours``. This
   keeps every item eligible at cycle start so the only thing that
   decides which items get searched is the page-selection algorithm.
2. Run N cycles against the mock with a known number of missing items
   (e.g. 50 items / pageSize=10 = 5 pages).
3. Read the search_log table, count "searched" rows per item_id, and
   report the distribution.

Key invariant: under stratified-shuffle + per-item cooldown, every
missing item should be searched a comparable number of times across N
cycles. Pages get visited uniformly per round, so items on each page
should drain at the same rate.

Run:

    .venv/bin/python -m tests.mock_arr.probe_cooldown
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import statistics
import tempfile
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import uvicorn
from cryptography.fernet import Fernet

import houndarr.engine.search_loop as _search_loop
from houndarr.crypto import encrypt
from houndarr.database import get_db, init_db, set_db_path
from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    LidarrSearchMode,
    MissingPolicy,
    ReadarrSearchMode,
    RuntimeSnapshot,
    SchedulePolicy,
    SearchOrder,
    SonarrSearchMode,
    UpgradePolicy,
    WhisparrV2SearchMode,
)
from tests.mock_arr.server import SeedConfig, create_app

_search_loop._INTER_SEARCH_DELAY_SECONDS = 0.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class _Server:
    server: uvicorn.Server
    thread: threading.Thread


def _start_mock(items: int, seed: int = 42) -> tuple[_Server, str]:
    port = _free_port()
    config = SeedConfig(
        seed=seed,
        sonarr_series=max(1, items // 10),
        sonarr_episodes_per_series=max(1, items // max(1, items // 10)),
    )
    app = create_app(config)
    uv_config = uvicorn.Config(
        app=app, host="127.0.0.1", port=port, log_level="error", access_log=False
    )
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock failed to start")
    return _Server(server=server, thread=thread), f"http://127.0.0.1:{port}"


def _stop_mock(handle: _Server) -> None:
    handle.server.should_exit = True
    handle.thread.join(timeout=5)


def _build_instance(*, base_url: str, batch_size: int, hourly_cap: int) -> Instance:
    return Instance(
        core=InstanceCore(
            id=1,
            name="Probe Sonarr",
            type=InstanceType.sonarr,
            url=f"{base_url}/sonarr",
            api_key="probe-key",
            enabled=True,
        ),
        missing=MissingPolicy(
            batch_size=batch_size,
            sleep_interval_mins=15,
            hourly_cap=hourly_cap,
            cooldown_days=7,
            post_release_grace_hrs=0,
            queue_limit=0,
            sonarr_search_mode=SonarrSearchMode.episode,
            lidarr_search_mode=LidarrSearchMode.album,
            readarr_search_mode=ReadarrSearchMode.book,
            whisparr_v2_search_mode=WhisparrV2SearchMode.episode,
        ),
        cutoff=CutoffPolicy(
            cutoff_enabled=False,
            cutoff_batch_size=5,
            cutoff_cooldown_days=21,
            cutoff_hourly_cap=1,
        ),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(search_order=SearchOrder.random),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    )


@contextlib.asynccontextmanager
async def _temp_db(master_key: bytes) -> AsyncIterator[None]:
    with tempfile.TemporaryDirectory() as data_dir:
        db_path = os.path.join(data_dir, "probe.db")
        set_db_path(db_path)
        await init_db()
        encrypted = encrypt("probe-key", master_key)
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                " VALUES (?, ?, ?, ?, ?)",
                (1, "Probe Sonarr", "sonarr", "http://localhost/sonarr", encrypted),
            )
            await conn.commit()
        yield


async def _read_dispatched_per_item() -> Counter[int]:
    """Count successful dispatches per item_id from the search_log."""
    counts: Counter[int] = Counter()
    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_id, COUNT(*) FROM search_log "
            "WHERE action = 'searched' AND item_id IS NOT NULL GROUP BY item_id"
        ) as cur:
            async for row in cur:
                counts[int(row[0])] = int(row[1])
    return counts


async def _missing_item_ids(client: httpx.AsyncClient, base_url: str) -> list[int]:
    """Pull the full set of missing item IDs from the mock for ground-truth."""
    resp = await client.get(
        f"{base_url}/sonarr/api/v3/wanted/missing",
        params={"page": 1, "pageSize": 2000, "monitored": "true"},
    )
    resp.raise_for_status()
    return [r["id"] for r in resp.json()["records"]]


async def _run_probe(
    *, items: int, cycles: int, batch_size: int, hourly_cap: int
) -> dict[str, Any]:
    """Drive ``cycles`` cycles, advancing simulated time so cooldowns expire.

    The clock advances ``cooldown_days + 1 hours`` per cycle so every item
    is eligible at the start of every cycle. That isolates the page-and-item
    selection algorithm from cooldown gating.
    """
    handle, base_url = _start_mock(items=items)
    master_key = Fernet.generate_key()
    cooldown_days = 7
    advance = timedelta(days=cooldown_days, hours=1)

    try:
        async with _temp_db(master_key):
            async with httpx.AsyncClient(timeout=30) as client:
                missing_ids = await _missing_item_ids(client, base_url)

                fake_time = [datetime(2026, 1, 1, tzinfo=UTC)]

                def _now() -> datetime:
                    return fake_time[0]

                instance = _build_instance(
                    base_url=base_url, batch_size=batch_size, hourly_cap=hourly_cap
                )

                with patch("houndarr.repositories.cooldowns._now_utc", _now):
                    for _ in range(cycles):
                        await run_instance_search(instance, master_key)
                        fake_time[0] += advance

                dispatched = await _read_dispatched_per_item()
    finally:
        _stop_mock(handle)

    # Restrict to the actual missing-ID universe.
    relevant = {item_id: dispatched.get(item_id, 0) for item_id in missing_ids}
    counts = list(relevant.values())
    n_missing = len(missing_ids)
    n_searched_at_least_once = sum(1 for c in counts if c > 0)
    n_never_searched = sum(1 for c in counts if c == 0)
    total = sum(counts)

    # Chi-square goodness-of-fit against uniform expected counts. This is
    # the right test for "is each item being treated equally" rather than
    # the naive max/min ratio, which collapses at small expected counts
    # because Poisson noise dominates.
    expected = total / n_missing if n_missing else 0
    chi_square = sum(((c - expected) ** 2) / expected for c in counts) if expected > 0 else 0.0
    # 5%-significance chi-square critical value at df = n_missing - 1.
    # For df > 30 the value is closely approximated by
    # 0.5 * (sqrt(2*df - 1) + 1.645) ** 2, the Wilson-Hilferty form.
    df = max(1, n_missing - 1)
    if df <= 30:
        # Hard-coded critical values for small df.
        small_crit = {
            1: 3.84,
            2: 5.99,
            3: 7.81,
            4: 9.49,
            5: 11.07,
            6: 12.59,
            7: 14.07,
            8: 15.51,
            9: 16.92,
            10: 18.31,
            15: 25.00,
            20: 31.41,
            25: 37.65,
            30: 43.77,
        }
        crit = small_crit.get(df, 1.5 * df)
    else:
        import math as _math

        crit = 0.5 * (_math.sqrt(2 * df - 1) + 1.645) ** 2

    # Coupon-collector reference: expected cycles to touch every item once.
    coupon_expected = sum(n_missing / k for k in range(1, n_missing + 1)) if n_missing else 0.0

    return {
        "items": items,
        "missing_count": n_missing,
        "cycles": cycles,
        "batch_size": batch_size,
        "hourly_cap": hourly_cap,
        "total_dispatches": total,
        "expected_per_item": expected,
        "items_touched": n_searched_at_least_once,
        "items_never_touched": n_never_searched,
        "min_per_item": min(counts) if counts else 0,
        "max_per_item": max(counts) if counts else 0,
        "mean_per_item": statistics.mean(counts) if counts else 0,
        "stdev_per_item": statistics.stdev(counts) if len(counts) > 1 else 0.0,
        "chi_square": chi_square,
        "chi_square_critical": crit,
        "coupon_expected_cycles": coupon_expected,
    }


def _verdict(result: dict[str, Any]) -> str:
    """Verdict based on chi-square test against uniform.

    Naive max/min ratios are misleading at small expected counts because
    Poisson noise dominates: with mean=2, observing some 0s and some 6s
    is consistent with uniformity, not bias. Chi-square correctly
    accounts for the expected variance.
    """
    if result["chi_square"] > result["chi_square_critical"]:
        return f"BIASED (chi^2={result['chi_square']:.1f} > {result['chi_square_critical']:.1f})"
    if result["expected_per_item"] < 5 and result["items_never_touched"] > 0:
        # Statistically uniform but the sample is too thin to actually
        # cover every item; flag it so the reader knows coverage is
        # incomplete even though no item is being algorithmically starved.
        return (
            "uniform-distribution (incomplete coverage at this sample size; "
            f"E[per-item]={result['expected_per_item']:.1f}, "
            f"need ~{result['coupon_expected_cycles']:.0f} cycles for full coverage)"
        )
    return "uniform"


async def main() -> None:
    """Run the probe at a few interesting library sizes and print summary."""
    print("Cooldown distribution probe")
    print("Time advances cooldown_days+1h per cycle, every item eligible each cycle.")
    print("Verifies stratified-shuffle delivers fair per-item attention.\n")

    # The selection algorithm visits pages uniformly (per the stratified-shuffle
    # probe) and dispatches up to ``batch_size`` items from each page.  When
    # the missing-item count divides evenly into the engine's page_size, every
    # page has the same number of items and every item has the same hit rate
    # per visit.  When it does not, the last (short) page over-selects its
    # items because the engine drains all of them per visit.  The configs
    # below demonstrate both regimes:
    #
    #   - items=200 / 100 missing / pageSize=10 -> 10 full pages of 10 items
    #     each. Expected: uniform.
    #   - items=400 / 200 missing / pageSize=10 -> 20 full pages.  Expected:
    #     uniform.
    #   - items=50 / 25 missing / pageSize=10 -> pages of 10, 10, 5 items.
    #     The 5-item page over-selects.
    #   - items=50 / 25 missing / batch=5 / pageSize=20 -> pages of 20 + 5.
    #     The 5-item page over-selects much more dramatically.
    configs = [
        # (items, cycles, batch_size, hourly_cap, label)
        (200, 400, 1, 1000, "100 missing / 10 full pages"),
        (400, 600, 1, 1000, "200 missing / 20 full pages"),
        (50, 200, 1, 1000, "25 missing / 3 pages (last has 5)"),
        (50, 200, 5, 1000, "25 missing / batch=5 / pageSize=20 (last has 5)"),
    ]
    rows: list[dict[str, Any]] = []
    for items, cycles, bs, cap, label in configs:
        print(f"=== items={items} cycles={cycles} batch={bs} cap={cap}  ({label}) ===")
        result = await _run_probe(items=items, cycles=cycles, batch_size=bs, hourly_cap=cap)
        rows.append(result)
        print(
            f"  missing={result['missing_count']:4d}  "
            f"dispatches={result['total_dispatches']:5d}  "
            f"E[per-item]={result['expected_per_item']:5.2f}  "
            f"touched={result['items_touched']:4d}  "
            f"never={result['items_never_touched']:4d}  "
            f"min={result['min_per_item']:3d}  max={result['max_per_item']:3d}  "
            f"mean={result['mean_per_item']:5.2f}  stdev={result['stdev_per_item']:5.2f}"
        )
        print(
            f"  chi^2={result['chi_square']:7.2f}  "
            f"critical={result['chi_square_critical']:7.2f}  "
            f"verdict={_verdict(result)}"
        )

    print("\n=================  COOLDOWN FAIRNESS SUMMARY  =================")
    print(
        f"{'items':>6}  {'cycles':>6}  {'bs':>3}  {'E[i]':>6}  "
        f"{'min':>4}  {'max':>4}  {'chi^2':>8}  {'crit':>8}  verdict"
    )
    for r in rows:
        print(
            f"{r['items']:>6}  {r['cycles']:>6}  {r['batch_size']:>3}  "
            f"{r['expected_per_item']:>6.2f}  "
            f"{r['min_per_item']:>4}  {r['max_per_item']:>4}  "
            f"{r['chi_square']:>8.2f}  {r['chi_square_critical']:>8.2f}  {_verdict(r)}"
        )


if __name__ == "__main__":
    asyncio.run(main())

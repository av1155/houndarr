"""Programmatic distribution probe for Houndarr's search algorithm.

Boots the seeded mock *arr server in-process, drives the real search
engine (``run_instance_search``) against it for many cycles at multiple
library sizes, and reports the per-page hit distribution captured from
the mock's ``/__page_log__`` endpoint.

Runs both ``random`` and ``chronological`` search orders so the two can
be compared directly. The mock is the source of truth for which pages
the engine actually fetched; the engine is the real production code
path, not a respx mock.

Run:

    .venv/bin/python -m tests.mock_arr.probe_distribution
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import socket
import statistics
import tempfile
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

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

# Zero the production inter-search delay so the probe runs at full speed.
# The 3-second default exists to spread indexer fan-out, which the mock
# does not care about.
_search_loop._INTER_SEARCH_DELAY_SECONDS = 0.0


def _free_port() -> int:
    """Return an unused TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class _Server:
    """Wrap ``uvicorn.Server`` so we can boot+stop it from a thread."""

    server: uvicorn.Server
    thread: threading.Thread


def _start_mock(items: int, seed: int = 42) -> tuple[_Server, str]:
    """Boot the mock on a free port. Returns the running server + base URL."""
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
    app = create_app(config)
    uv_config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock server failed to start within 5s")
    return _Server(server=server, thread=thread), f"http://127.0.0.1:{port}"


def _stop_mock(handle: _Server) -> None:
    """Signal the uvicorn server to exit and wait for the thread to join."""
    handle.server.should_exit = True
    handle.thread.join(timeout=5)


def _build_instance(
    *,
    base_url: str,
    instance_id: int,
    search_order: SearchOrder,
    batch_size: int,
    hourly_cap: int,
) -> Instance:
    """Construct an in-memory Instance pointing at the mock."""
    return Instance(
        core=InstanceCore(
            id=instance_id,
            name=f"Probe Sonarr {instance_id}",
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
        schedule=SchedulePolicy(search_order=search_order),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    )


@contextlib.asynccontextmanager
async def _temp_db(master_key: bytes) -> AsyncIterator[None]:
    """Create a fresh SQLite DB + insert a Sonarr instance row for FKs."""
    with tempfile.TemporaryDirectory() as data_dir:
        db_path = os.path.join(data_dir, "probe.db")
        set_db_path(db_path)
        await init_db()
        encrypted = encrypt("probe-key", master_key)
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                " VALUES (?, ?, ?, ?, ?)",
                (1, "Probe Sonarr 1", "sonarr", "http://localhost/sonarr", encrypted),
            )
            await conn.commit()
        yield


async def _wipe_cooldowns_and_log() -> None:
    """Clear cooldown + search_log rows so each cycle sees a fresh backlog.

    Without this the engine would hit cooldown on previously-searched items
    and skip them, contaminating the page-distribution measurement.
    """
    async with get_db() as conn:
        await conn.execute("DELETE FROM cooldowns")
        await conn.execute("DELETE FROM search_log")
        await conn.commit()


async def _reset_mock(client: httpx.AsyncClient, base_url: str) -> None:
    """Wipe the mock's per-app logs."""
    await client.post(f"{base_url}/__reset__/sonarr")


async def _read_page_log(client: httpx.AsyncClient, base_url: str) -> list[tuple[str, int, int]]:
    """Pull the mock's page-request log."""
    resp = await client.get(f"{base_url}/__page_log__/sonarr")
    resp.raise_for_status()
    body = resp.json()
    return [(kind, page, ps) for kind, page, ps in body["entries"]]


@dataclass(slots=True)
class _CycleStats:
    """Aggregated page hits across many cycles, plus per-cycle start pages.

    ``start_page_hits`` measures the algorithm's randint pick directly.
    ``page_hits`` aggregates every visited page so we can see the wrap
    pattern when the engine walks multiple pages.
    """

    start_page_hits: Counter[int]
    page_hits: Counter[int]
    total_cycles: int


async def _run_cycles(
    *,
    base_url: str,
    cycles: int,
    instance: Instance,
    master_key: bytes,
    client: httpx.AsyncClient,
) -> _CycleStats:
    """Run ``cycles`` of ``run_instance_search`` and return distribution stats.

    Cooldowns are wiped between cycles so the engine sees a fresh backlog
    every time. Without this, a 7-day cooldown would mark every item
    searched in cycle N ineligible for cycles N+1...N+M, biasing the
    page-fetch measurement away from the underlying algorithm.

    Two histograms are returned. ``start_page_hits`` records the FIRST
    real-pageSize fetch of each cycle, which is the page the algorithm
    picked via ``random.randint``. ``page_hits`` records every visited
    page so we can also see how the wrap-once walk distributes hits.
    """
    start_page_hits: Counter[int] = Counter()
    page_hits: Counter[int] = Counter()
    for _ in range(cycles):
        await _wipe_cooldowns_and_log()
        await _reset_mock(client, base_url)
        await run_instance_search(instance, master_key)
        log = await _read_page_log(client, base_url)
        first_seen = False
        for kind, page, page_size_entry in log:
            # Filter out the random-mode probe call (always page=1 page_size=1)
            # so it does not bias the histogram toward page 1.
            if kind != "missing" or page_size_entry <= 1:
                continue
            page_hits[page] += 1
            if not first_seen:
                start_page_hits[page] += 1
                first_seen = True
    return _CycleStats(
        start_page_hits=start_page_hits,
        page_hits=page_hits,
        total_cycles=cycles,
    )


def _max_min_ratio(hits: Counter[int]) -> float:
    """Ratio of the most-hit page count to the least-hit page count.

    A perfectly uniform distribution returns 1.0. Larger means more bias.
    """
    if not hits:
        return 0.0
    counts = list(hits.values())
    if min(counts) == 0:
        return float("inf")
    return max(counts) / min(counts)


def _chi_square(hits: Counter[int], n_pages: int) -> float:
    """Pearson chi-square against the uniform distribution.

    Lower means closer to uniform. The page-set is the keys of ``hits``,
    padded out to ``n_pages`` so missing pages count as zero hits.
    """
    total = sum(hits.values())
    if total == 0 or n_pages == 0:
        return 0.0
    expected = total / n_pages
    return sum(((hits.get(p, 0) - expected) ** 2) / expected for p in range(1, n_pages + 1))


def _format_histogram(hits: Counter[int], n_pages: int) -> str:
    """Render a compact ASCII histogram of page hits."""
    if not hits:
        return "(no data)"
    counts = [hits.get(p, 0) for p in range(1, n_pages + 1)]
    peak = max(counts) if counts else 0
    if peak == 0:
        return "(all zero)"
    rows = []
    for page, count in enumerate(counts, start=1):
        bar = "#" * int(20 * count / peak)
        rows.append(f"  page {page:2d}: {count:4d}  {bar}")
    return "\n".join(rows)


async def _probe_one(
    *,
    base_url: str,
    client: httpx.AsyncClient,
    items: int,
    cycles: int,
    search_order: SearchOrder,
    master_key: bytes,
    page_size: int,
    batch_size: int,
) -> dict[str, Any]:
    """Run one (items, mode) configuration and return its summary stats."""
    n_missing = items // 2
    n_pages = max(1, math.ceil(n_missing / page_size))
    instance = _build_instance(
        base_url=base_url,
        instance_id=1,
        search_order=search_order,
        batch_size=batch_size,
        hourly_cap=batch_size * cycles + 1000,
    )
    stats = await _run_cycles(
        base_url=base_url,
        cycles=cycles,
        instance=instance,
        master_key=master_key,
        client=client,
    )
    start_counts = [stats.start_page_hits.get(p, 0) for p in range(1, n_pages + 1)]
    return {
        "items": items,
        "missing": n_missing,
        "page_size": page_size,
        "n_pages": n_pages,
        "mode": search_order.value,
        "cycles": cycles,
        "total_starts": sum(stats.start_page_hits.values()),
        "min_starts": min(start_counts) if start_counts else 0,
        "max_starts": max(start_counts) if start_counts else 0,
        "mean_starts": statistics.mean(start_counts) if start_counts else 0,
        "stdev_starts": statistics.stdev(start_counts) if len(start_counts) > 1 else 0,
        "max_min_ratio": _max_min_ratio(stats.start_page_hits),
        "chi_square": _chi_square(stats.start_page_hits, n_pages),
        "histogram": _format_histogram(stats.start_page_hits, n_pages),
        "all_hits": dict(sorted(stats.page_hits.items())),
    }


async def main() -> None:
    """Run every (items, mode) configuration and print the summary table.

    The configurations span the four behaviour regimes:
    - small library where ``N < K`` and the wrap-once rule biases vs page 1
    - boundary where ``N == K``
    - mid-size library where ``N`` is just above ``K``
    - large library where ``N >> K``
    """
    master_key = Fernet.generate_key()
    cycles = 400
    batch_size = 1
    page_size = 10

    configs: list[tuple[int, str]] = [
        (8, "N=1 (degenerate)"),
        (20, "N=1 (boundary, all on one page)"),
        (40, "N=2 (small, biased per the math)"),
        (60, "N=3 (small, biased)"),
        (80, "N=4 (small, biased)"),
        (100, "N=5 (boundary, expected uniform)"),
        (120, "N=6 (just above K, expected uniform)"),
        (200, "N=10 (mid, expected uniform)"),
        (1000, "N=50 (large, expected uniform)"),
    ]

    rows: list[dict[str, Any]] = []
    for items, label in configs:
        print(f"\n=== probing items={items} ({label}) ===")
        handle, base_url = _start_mock(items=items)
        try:
            async with _temp_db(master_key):
                async with httpx.AsyncClient(timeout=30) as client:
                    for search_order in (
                        SearchOrder.random,
                        SearchOrder.chronological,
                    ):
                        result = await _probe_one(
                            base_url=base_url,
                            client=client,
                            items=items,
                            cycles=cycles,
                            search_order=search_order,
                            master_key=master_key,
                            page_size=page_size,
                            batch_size=batch_size,
                        )
                        result["label"] = label
                        rows.append(result)
                        print(
                            f"  mode={result['mode']:14s}  "
                            f"N={result['n_pages']:2d}  "
                            f"starts={result['total_starts']:5d}  "
                            f"min={result['min_starts']:4d}  "
                            f"max={result['max_starts']:4d}  "
                            f"max/min={result['max_min_ratio']:6.2f}  "
                            f"chi^2={result['chi_square']:7.2f}"
                        )
                        if result["n_pages"] <= 12:
                            print(result["histogram"])
                        print(f"    all-page hits: {result['all_hits']}")
        finally:
            _stop_mock(handle)

    print("\n\n=========================  START-PAGE SUMMARY  =========================")
    print(
        f"{'items':>6}  {'N':>3}  {'mode':14s}  "
        f"{'min':>4}  {'max':>4}  {'max/min':>8}  {'chi^2':>8}  verdict"
    )
    for r in rows:
        if r["n_pages"] == 1:
            verdict = "trivial (single page)"
        elif r["mode"] == "chronological":
            verdict = "expected: always page 1 (state wiped between cycles)"
        else:
            ratio = r["max_min_ratio"]
            chi2 = r["chi_square"]
            verdict = "uniform" if ratio < 1.5 and chi2 < 3 * r["n_pages"] else "BIASED"
        print(
            f"{r['items']:>6}  {r['n_pages']:>3}  {r['mode']:14s}  "
            f"{r['min_starts']:>4}  {r['max_starts']:>4}  "
            f"{r['max_min_ratio']:>8.2f}  {r['chi_square']:>8.2f}  {verdict}"
        )


if __name__ == "__main__":
    asyncio.run(main())

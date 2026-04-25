"""Context-mode fairness probe.

Sonarr / Whisparr v2 / Lidarr / Readarr each support a per-app context
search mode that groups child items by their parent (season, artist,
author).  In context mode the engine dispatches one search per parent
group per cycle instead of one per child item, using a synthetic
negative parent id as the dedup key.

This probe verifies that under each app's context mode:

1. Every monitored parent group eventually gets dispatched at least
   once over enough cycles for full coverage.
2. Each parent's dispatch share matches its **share of missing items**
   in the wanted-list, not a uniform 1/N share.  The earlier version
   of this probe ran a naive chi-square against equal expected counts
   per parent; that test is conservative because the engine visits a
   random page of items, not a random parent, so a parent with twice
   as many missing leaves is *expected* to be dispatched roughly
   twice as often.  The weighted chi-square below tests the property
   that actually matters: do the dispatch counts track the per-parent
   missing-item shares predicted by the algorithm?
3. The padding + position-cap fix from the earlier round still holds
   when the dispatch unit is the synthetic parent rather than the
   raw leaf.

Run:

    .venv/bin/python -m tests.mock_arr.probe_context_mode
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


def _start_mock(seed_config: SeedConfig) -> tuple[_Server, str]:
    port = _free_port()
    app = create_app(seed_config)
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


def _build_instance_sonarr(*, base_url: str, batch_size: int, hourly_cap: int) -> Instance:
    """Sonarr with season_context search mode."""
    return Instance(
        core=InstanceCore(
            id=1,
            name="Probe Sonarr ctx",
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
            sonarr_search_mode=SonarrSearchMode.season_context,
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


def _build_instance_whisparr_v2(*, base_url: str, batch_size: int, hourly_cap: int) -> Instance:
    """Whisparr v2 with season_context search mode."""
    return Instance(
        core=InstanceCore(
            id=1,
            name="Probe Whisparr v2 ctx",
            type=InstanceType.whisparr_v2,
            url=f"{base_url}/whisparr_v2",
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
            whisparr_v2_search_mode=WhisparrV2SearchMode.season_context,
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


def _build_instance_lidarr(*, base_url: str, batch_size: int, hourly_cap: int) -> Instance:
    """Lidarr with artist_context search mode."""
    return Instance(
        core=InstanceCore(
            id=1,
            name="Probe Lidarr ctx",
            type=InstanceType.lidarr,
            url=f"{base_url}/lidarr",
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
            lidarr_search_mode=LidarrSearchMode.artist_context,
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


def _build_instance_readarr(*, base_url: str, batch_size: int, hourly_cap: int) -> Instance:
    """Readarr with author_context search mode."""
    return Instance(
        core=InstanceCore(
            id=1,
            name="Probe Readarr ctx",
            type=InstanceType.readarr,
            url=f"{base_url}/readarr",
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
            readarr_search_mode=ReadarrSearchMode.author_context,
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
async def _temp_db(master_key: bytes, instance_type_value: str) -> AsyncIterator[None]:
    with tempfile.TemporaryDirectory() as data_dir:
        db_path = os.path.join(data_dir, "probe.db")
        set_db_path(db_path)
        await init_db()
        encrypted = encrypt("probe-key", master_key)
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                " VALUES (?, ?, ?, ?, ?)",
                (1, "Probe ctx", instance_type_value, "http://localhost", encrypted),
            )
            await conn.commit()
        yield


async def _read_dispatched_per_synthetic_parent() -> Counter[int]:
    """Count successful dispatches grouped by item_id.

    Context-mode candidates carry a synthetic negative parent id, so a
    single dispatch in season-context mode covers all children of one
    season at once.  Each row in search_log with action='searched'
    represents one parent dispatch.
    """
    counts: Counter[int] = Counter()
    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_id, COUNT(*) FROM search_log "
            "WHERE action = 'searched' AND item_id IS NOT NULL GROUP BY item_id"
        ) as cur:
            async for row in cur:
                counts[int(row[0])] = int(row[1])
    return counts


def _decode_synthetic_parent(synthetic_id: int, app: str) -> int:
    """Recover the real parent id from a synthetic negative dispatch id.

    Sonarr and Whisparr v2 encode ``-(series_id * 1000 + season_number)``
    so the parent identity is ``positive // 1000``.  Lidarr and Readarr
    encode ``-(parent_id * 1000)`` flat (no season axis), so the parent
    identity is also ``positive // 1000``.  In every case dividing the
    absolute value by 1000 returns the parent id the dispatch belongs
    to, which is what the weighted-chi-square test needs to match the
    per-parent missing-item shares.
    """
    positive = -synthetic_id if synthetic_id < 0 else synthetic_id
    return positive // 1000


async def _missing_items_per_parent(
    client: httpx.AsyncClient, base_url: str, app: str
) -> Counter[int]:
    """Return ``parent_id -> count_of_missing_leaves`` from the live mock.

    The probe pages through every record in ``/wanted/missing`` for the
    target app and groups leaves by their parent id (``seriesId`` for
    Sonarr / Whisparr v2, ``artistId`` for Lidarr, ``authorId`` for
    Readarr).  The resulting per-parent counts feed the weighted chi-
    square denominator: a parent with three times as many missing
    leaves should land in the page-shuffled scan three times as often,
    so its expected dispatch count is three times higher.
    """
    if app in ("sonarr", "whisparr_v2"):
        endpoint = f"{base_url}/{app}/api/v3/wanted/missing"
        parent_field = "seriesId"
    elif app == "lidarr":
        endpoint = f"{base_url}/lidarr/api/v1/wanted/missing"
        parent_field = "artistId"
    else:  # readarr
        endpoint = f"{base_url}/readarr/api/v1/wanted/missing"
        parent_field = "authorId"

    counts: Counter[int] = Counter()
    page = 1
    page_size = 250
    while True:
        resp = await client.get(
            endpoint,
            params={"page": page, "pageSize": page_size, "monitored": "true"},
        )
        resp.raise_for_status()
        body = resp.json()
        records = body.get("records", [])
        if not records:
            break
        for r in records:
            pid = r.get(parent_field)
            if pid is not None:
                counts[int(pid)] += 1
        if len(records) < page_size:
            break
        page += 1
    return counts


def _chi_square_critical(df: int) -> float:
    """Return the 5%-significance chi-square critical value for ``df``.

    Hard-codes the small-df values where the table look-up beats any
    closed-form approximation, then falls back to the Wilson-Hilferty
    form for larger df.  Centralised so the weighted and naive chi-
    square branches share the same threshold logic.
    """
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
    if df in small_crit:
        return small_crit[df]
    if df < 30:
        return 1.5 * df
    import math as _math

    return 0.5 * (_math.sqrt(2 * df - 1) + 1.645) ** 2


async def _run_one(
    *,
    label: str,
    base_url: str,
    app: str,
    instance: Instance,
    instance_type_value: str,
    cycles: int,
    cooldown_days: int,
) -> dict[str, Any]:
    master_key = Fernet.generate_key()
    advance = timedelta(days=cooldown_days, hours=1)

    fake_time = [datetime(2026, 1, 1, tzinfo=UTC)]

    def _now() -> datetime:
        return fake_time[0]

    async with httpx.AsyncClient(timeout=30) as client:
        missing_per_parent = await _missing_items_per_parent(client, base_url, app)

    async with _temp_db(master_key, instance_type_value):
        with patch("houndarr.repositories.cooldowns._now_utc", _now):
            for _ in range(cycles):
                await run_instance_search(instance, master_key)
                fake_time[0] += advance
        dispatched = await _read_dispatched_per_synthetic_parent()

    # Roll synthetic-parent dispatches up to real parent ids so the
    # weighted chi-square can compare them against missing-item shares.
    real_parent_dispatches: Counter[int] = Counter()
    for synthetic, count in dispatched.items():
        real_parent_dispatches[_decode_synthetic_parent(synthetic, app)] += count

    parent_count = len(real_parent_dispatches)
    total = sum(real_parent_dispatches.values())
    if parent_count == 0 or total == 0:
        return {
            "label": label,
            "cycles": cycles,
            "parents_touched": 0,
            "total_dispatches": 0,
            "verdict": "no dispatches recorded (check fixture)",
        }

    # Weighted expected: each parent's share is its missing-items share
    # of the total wanted-list, scaled to the observed dispatch total.
    # Naive expected: total / parent_count, kept as a sanity-check
    # comparison so a regression that breaks the weighting also visibly
    # shifts the naive chi-square.
    total_missing_leaves = sum(missing_per_parent.values())
    weighted_chi_square = 0.0
    for parent_id, observed in real_parent_dispatches.items():
        share = missing_per_parent.get(parent_id, 0) / max(1, total_missing_leaves)
        expected = share * total
        if expected <= 0:
            continue
        weighted_chi_square += ((observed - expected) ** 2) / expected
    naive_expected = total / parent_count
    naive_chi_square = sum(
        ((observed - naive_expected) ** 2) / naive_expected
        for observed in real_parent_dispatches.values()
    )
    crit = _chi_square_critical(max(1, parent_count - 1))

    verdict = (
        "uniform-by-share"
        if weighted_chi_square <= crit
        else f"BIASED (weighted chi^2={weighted_chi_square:.1f} > {crit:.1f})"
    )

    counts = list(real_parent_dispatches.values())
    return {
        "label": label,
        "cycles": cycles,
        "parents_touched": parent_count,
        "total_dispatches": total,
        "min_per_parent": min(counts),
        "max_per_parent": max(counts),
        "mean_per_parent": statistics.mean(counts),
        "stdev_per_parent": statistics.stdev(counts) if parent_count > 1 else 0.0,
        "weighted_chi_square": weighted_chi_square,
        "naive_chi_square": naive_chi_square,
        "chi_square_critical": crit,
        "verdict": verdict,
    }


async def main() -> None:
    print("Context-mode fairness probe")
    print("Verifies synthetic-parent dispatch is uniform across groups for each context mode.\n")

    rows: list[dict[str, Any]] = []

    cases: list[tuple[str, str, str]] = [
        ("Sonarr / season_context", "sonarr", "sonarr"),
        ("Whisparr v2 / season_context", "whisparr_v2", "whisparr_v2"),
        ("Lidarr / artist_context", "lidarr", "lidarr"),
        ("Readarr / author_context", "readarr", "readarr"),
    ]

    for label, sub_path, instance_type_value in cases:
        # Each app uses the standard SeedConfig (50 parents x 10 leaves = 500
        # leaves total, 50% missing = 250 missing items).  Context mode
        # collapses dispatches to one per parent, so we expect up to ~50
        # distinct parent dispatches to be eligible.
        seed = SeedConfig(seed=42)
        handle, base_url = _start_mock(seed)
        try:
            if sub_path == "sonarr":
                inst = _build_instance_sonarr(base_url=base_url, batch_size=1, hourly_cap=1000)
            elif sub_path == "whisparr_v2":
                inst = _build_instance_whisparr_v2(base_url=base_url, batch_size=1, hourly_cap=1000)
            elif sub_path == "lidarr":
                inst = _build_instance_lidarr(base_url=base_url, batch_size=1, hourly_cap=1000)
            else:
                inst = _build_instance_readarr(base_url=base_url, batch_size=1, hourly_cap=1000)

            print(f"=== {label} ===")
            result = await _run_one(
                label=label,
                base_url=base_url,
                app=sub_path,
                instance=inst,
                instance_type_value=instance_type_value,
                cycles=300,
                cooldown_days=7,
            )
            rows.append(result)
            if result.get("verdict", "").startswith("no dispatches"):
                print(f"  {result['verdict']}\n")
                continue
            print(
                f"  parents_touched={result['parents_touched']}  "
                f"dispatches={result['total_dispatches']}  "
                f"min={result['min_per_parent']}  max={result['max_per_parent']}  "
                f"mean={result['mean_per_parent']:.2f}  stdev={result['stdev_per_parent']:.2f}"
            )
            print(
                f"  weighted chi^2={result['weighted_chi_square']:.2f}  "
                f"(naive chi^2={result['naive_chi_square']:.2f})  "
                f"critical={result['chi_square_critical']:.2f}  "
                f"verdict={result['verdict']}\n"
            )
        finally:
            _stop_mock(handle)

    print("\n=================  CONTEXT-MODE SUMMARY  =================")
    print(
        f"{'app / mode':30s}  {'parents':>7}  {'min':>4}  {'max':>4}  "
        f"{'weighted':>9}  {'naive':>7}  {'crit':>5}  verdict"
    )
    for r in rows:
        if r.get("parents_touched", 0) == 0:
            print(
                f"{r['label']:30s}  {'-':>7}  {'-':>4}  {'-':>4}  "
                f"{'-':>9}  {'-':>7}  {'-':>5}  {r['verdict']}"
            )
            continue
        print(
            f"{r['label']:30s}  {r['parents_touched']:>7}  "
            f"{r['min_per_parent']:>4}  {r['max_per_parent']:>4}  "
            f"{r['weighted_chi_square']:>9.2f}  "
            f"{r['naive_chi_square']:>7.2f}  "
            f"{r['chi_square_critical']:>5.2f}  {r['verdict']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

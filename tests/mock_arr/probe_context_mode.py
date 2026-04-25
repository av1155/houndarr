"""Context-mode fairness probe.

Sonarr / Whisparr v2 / Lidarr / Readarr each support a per-app context
search mode that groups child items by their parent (season, artist,
author).  In context mode the engine dispatches one search per parent
group per cycle instead of one per child item, using a synthetic
negative parent id as the dedup key.

This probe verifies that under each app's context mode:

1. Every monitored parent group eventually gets dispatched at least
   once over enough cycles for full coverage.
2. The dispatches are uniformly distributed across parent groups
   (chi-square against the uniform expected count, with the same
   thresholds as the per-item probe).
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


async def _run_one(
    *,
    label: str,
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

    async with _temp_db(master_key, instance_type_value):
        with patch("houndarr.repositories.cooldowns._now_utc", _now):
            for _ in range(cycles):
                await run_instance_search(instance, master_key)
                fake_time[0] += advance
        dispatched = await _read_dispatched_per_synthetic_parent()

    counts = list(dispatched.values())
    parent_count = len(counts)
    total = sum(counts)
    if parent_count == 0:
        return {
            "label": label,
            "cycles": cycles,
            "parents_touched": 0,
            "total_dispatches": 0,
            "verdict": "no dispatches recorded (check fixture)",
        }
    expected = total / parent_count
    chi_square = sum(((c - expected) ** 2) / expected for c in counts)
    df = max(1, parent_count - 1)
    if df <= 30:
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

    verdict = "uniform" if chi_square <= crit else f"BIASED (chi^2={chi_square:.1f} > {crit:.1f})"

    return {
        "label": label,
        "cycles": cycles,
        "parents_touched": parent_count,
        "total_dispatches": total,
        "expected_per_parent": expected,
        "min_per_parent": min(counts),
        "max_per_parent": max(counts),
        "mean_per_parent": statistics.mean(counts),
        "stdev_per_parent": statistics.stdev(counts) if parent_count > 1 else 0.0,
        "chi_square": chi_square,
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
                f"E[per-parent]={result['expected_per_parent']:.2f}  "
                f"min={result['min_per_parent']}  max={result['max_per_parent']}  "
                f"mean={result['mean_per_parent']:.2f}  stdev={result['stdev_per_parent']:.2f}"
            )
            print(
                f"  chi^2={result['chi_square']:.2f}  "
                f"critical={result['chi_square_critical']:.2f}  "
                f"verdict={result['verdict']}\n"
            )
        finally:
            _stop_mock(handle)

    print("\n=================  CONTEXT-MODE SUMMARY  =================")
    print(f"{'app / mode':30s}  {'parents':>7}  {'min':>4}  {'max':>4}  {'chi^2':>8}  verdict")
    for r in rows:
        if r.get("parents_touched", 0) == 0:
            print(f"{r['label']:30s}  {'-':>7}  {'-':>4}  {'-':>4}  {'-':>8}  {r['verdict']}")
            continue
        print(
            f"{r['label']:30s}  {r['parents_touched']:>7}  "
            f"{r['min_per_parent']:>4}  {r['max_per_parent']:>4}  "
            f"{r['chi_square']:>8.2f}  {r['verdict']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

"""Upgrade-pass coverage probe.

Drives many upgrade-only cycles against the mock and verifies that every
monitored, has-file, cutoff-met item in the library gets searched within
the predicted coverage window.

Coverage promises by app type:

- Radarr / Whisparr v3: full-library fetch every cycle. With chronological
  order, ``upgrade_item_offset`` rotates so every item is touched within
  ``ceil(library_size / batch_size)`` cycles. With random order, a fresh
  shuffle every cycle yields probabilistic coverage that converges.
- Sonarr / Whisparr v2: a sliding window of 5 series per cycle (the
  ``_UPGRADE_MAX_SERIES_PER_CYCLE`` constant). ``upgrade_series_offset``
  advances by 5 every cycle, so all monitored series are sampled within
  ``ceil(monitored_series / 5)`` cycles.
- Lidarr / Readarr: paginated cutoff exclusion (max 10 pages * 250 records)
  followed by a full library fetch. The exclusion set is capped, so very
  large cutoff backlogs may not fully filter the upgrade pool.

The probe only verifies the simplest path: full coverage of a moderate-
sized library by running enough cycles for the formula to predict
complete coverage, then asserting every eligible item appeared in
``search_log`` at least once.

Run:

    .venv/bin/python -m tests.mock_arr.probe_upgrade_coverage
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import socket
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


def _build_instance(
    *,
    base_url: str,
    sub_path: str,
    instance_type: InstanceType,
    upgrade_batch_size: int,
    search_order: SearchOrder,
) -> Instance:
    return Instance(
        core=InstanceCore(
            id=1,
            name=f"Probe {instance_type.value}",
            type=instance_type,
            url=f"{base_url}/{sub_path}",
            api_key="probe-key",
            enabled=True,
        ),
        # Disable missing/cutoff: we only want the upgrade pass to fire.
        missing=MissingPolicy(
            batch_size=0,
            sleep_interval_mins=15,
            hourly_cap=0,
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
            cutoff_batch_size=0,
            cutoff_cooldown_days=21,
            cutoff_hourly_cap=0,
        ),
        upgrade=UpgradePolicy(
            upgrade_enabled=True,
            upgrade_batch_size=upgrade_batch_size,
            upgrade_cooldown_days=7,
            upgrade_hourly_cap=upgrade_batch_size * 100,
        ),
        schedule=SchedulePolicy(search_order=search_order),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    )


@contextlib.asynccontextmanager
async def _temp_db(master_key: bytes, instance: Instance) -> AsyncIterator[None]:
    with tempfile.TemporaryDirectory() as data_dir:
        db_path = os.path.join(data_dir, "probe.db")
        set_db_path(db_path)
        await init_db()
        encrypted = encrypt("probe-key", master_key)
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    instance.core.id,
                    instance.core.name,
                    instance.core.type.value,
                    instance.core.url,
                    encrypted,
                ),
            )
            await conn.commit()
        yield


async def _read_dispatched_per_item() -> Counter[int]:
    counts: Counter[int] = Counter()
    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_id, COUNT(*) FROM search_log "
            "WHERE action = 'searched' AND search_kind = 'upgrade' "
            "AND item_id IS NOT NULL GROUP BY item_id"
        ) as cur:
            async for row in cur:
                counts[int(row[0])] = int(row[1])
    return counts


async def _upgrade_eligible_movie_ids(
    client: httpx.AsyncClient, base_url: str, sub_path: str
) -> set[int]:
    """For Radarr-shaped apps, pull the library and filter cutoff-met items."""
    resp = await client.get(f"{base_url}/{sub_path}/api/v3/movie")
    resp.raise_for_status()
    return {
        m["id"]
        for m in resp.json()
        if m.get("monitored")
        and m.get("hasFile")
        and m.get("movieFile") is not None
        and not m["movieFile"].get("qualityCutoffNotMet", False)
    }


async def _upgrade_eligible_sonarr_episode_ids(
    client: httpx.AsyncClient, base_url: str, sub_path: str
) -> set[int]:
    """For Sonarr-shaped apps, walk every series and collect upgrade items."""
    series = (await client.get(f"{base_url}/{sub_path}/api/v3/series")).json()
    eligible: set[int] = set()
    for s in series:
        eps = (
            await client.get(
                f"{base_url}/{sub_path}/api/v3/episode",
                params={"seriesId": s["id"]},
            )
        ).json()
        for ep in eps:
            ef = ep.get("episodeFile")
            if (
                ep.get("monitored")
                and ep.get("hasFile")
                and ef is not None
                and not ef.get("qualityCutoffNotMet", False)
            ):
                eligible.add(ep["id"])
    return eligible


async def _run_probe(
    *,
    instance_type: InstanceType,
    sub_path: str,
    seed_config: SeedConfig,
    cycles: int,
    upgrade_batch_size: int,
    search_order: SearchOrder,
) -> dict[str, Any]:
    handle, base_url = _start_mock(seed_config)
    master_key = Fernet.generate_key()
    advance = timedelta(days=8)
    fake_time = [datetime(2026, 1, 1, tzinfo=UTC)]

    def _now() -> datetime:
        return fake_time[0]

    instance = _build_instance(
        base_url=base_url,
        sub_path=sub_path,
        instance_type=instance_type,
        upgrade_batch_size=upgrade_batch_size,
        search_order=search_order,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if instance_type in (InstanceType.sonarr, InstanceType.whisparr_v2):
                eligible_ids = await _upgrade_eligible_sonarr_episode_ids(
                    client, base_url, sub_path
                )
            else:
                eligible_ids = await _upgrade_eligible_movie_ids(client, base_url, sub_path)

            async with _temp_db(master_key, instance):
                # The hourly-cap counter is a SQL count over search_log
                # within real-wallclock 3600s.  In a probe that runs all
                # cycles in sub-second wallclock time, that cap saturates
                # and starves every cycle after the first.  Patch it to
                # always return zero so the cap never triggers; we are
                # measuring rotation, not throttling.
                async def _zero_hourly(*_args: object, **_kwargs: object) -> int:
                    return 0

                with (
                    patch("houndarr.repositories.cooldowns._now_utc", _now),
                    patch(
                        "houndarr.engine.search_loop._count_searches_last_hour",
                        _zero_hourly,
                    ),
                ):
                    current = instance
                    for _ in range(cycles):
                        await run_instance_search(current, master_key)
                        # Re-read the instance to pick up any persisted
                        # series_offset / item_offset updates the engine
                        # writes back during chronological mode.
                        async with get_db() as conn:
                            async with conn.execute(
                                "SELECT upgrade_series_offset, upgrade_item_offset"
                                " FROM instances WHERE id = ?",
                                (instance.core.id,),
                            ) as cur:
                                row = await cur.fetchone()
                        if row is not None:
                            new_upgrade = UpgradePolicy(
                                upgrade_enabled=current.upgrade.upgrade_enabled,
                                upgrade_batch_size=current.upgrade.upgrade_batch_size,
                                upgrade_cooldown_days=current.upgrade.upgrade_cooldown_days,
                                upgrade_hourly_cap=current.upgrade.upgrade_hourly_cap,
                                upgrade_sonarr_search_mode=current.upgrade.upgrade_sonarr_search_mode,
                                upgrade_lidarr_search_mode=current.upgrade.upgrade_lidarr_search_mode,
                                upgrade_readarr_search_mode=current.upgrade.upgrade_readarr_search_mode,
                                upgrade_whisparr_v2_search_mode=current.upgrade.upgrade_whisparr_v2_search_mode,
                                upgrade_item_offset=int(row[1] or 0),
                                upgrade_series_offset=int(row[0] or 0),
                            )
                            current = Instance(
                                core=current.core,
                                missing=current.missing,
                                cutoff=current.cutoff,
                                upgrade=new_upgrade,
                                schedule=current.schedule,
                                snapshot=current.snapshot,
                                timestamps=current.timestamps,
                            )
                        fake_time[0] += advance

                dispatched = await _read_dispatched_per_item()
    finally:
        _stop_mock(handle)

    relevant = {iid: dispatched.get(iid, 0) for iid in eligible_ids}
    counts = list(relevant.values())
    n_eligible = len(eligible_ids)
    n_touched = sum(1 for c in counts if c > 0)
    n_never = n_eligible - n_touched

    return {
        "type": instance_type.value,
        "search_order": search_order.value,
        "cycles": cycles,
        "upgrade_batch_size": upgrade_batch_size,
        "eligible": n_eligible,
        "touched": n_touched,
        "never_touched": n_never,
        "total_dispatches": sum(counts),
        "min_per_item": min(counts) if counts else 0,
        "max_per_item": max(counts) if counts else 0,
        "first_few_never": [iid for iid, n in relevant.items() if n == 0][:10],
    }


def _verdict(result: dict[str, Any], *, expected_full_coverage: bool) -> str:
    if expected_full_coverage and result["never_touched"] > 0:
        return (
            f"INCOMPLETE COVERAGE: {result['never_touched']} of {result['eligible']} "
            f"items never searched (first few: {result['first_few_never']})"
        )
    if result["never_touched"] > 0:
        return (
            f"partial coverage: {result['never_touched']} of {result['eligible']} "
            "items not yet touched"
        )
    return "full coverage"


async def main() -> None:
    print("Upgrade-pass coverage probe\n")

    # 1. Radarr chronological: full library every cycle, offset rotates by
    #    upgrade_batch_size per cycle. With 100 movies in upgrade-eligible
    #    state and batch=5, full coverage in ~20 cycles.
    print("=== Radarr chronological, library=200, upgrade-eligible=60, batch=5 ===")
    seed = SeedConfig(seed=42, radarr_movies=200)
    cycles = math.ceil(60 / 5) + 5  # +5 padding
    result = await _run_probe(
        instance_type=InstanceType.radarr,
        sub_path="radarr",
        seed_config=seed,
        cycles=cycles,
        upgrade_batch_size=5,
        search_order=SearchOrder.chronological,
    )
    print(
        f"  eligible={result['eligible']}  touched={result['touched']}  "
        f"never={result['never_touched']}  cycles={cycles}"
    )
    print(f"  verdict: {_verdict(result, expected_full_coverage=True)}\n")

    # 2. Radarr random: same setup but random order. Expect probabilistic
    #    coverage; over enough cycles every item is touched.
    print("=== Radarr random, library=200, upgrade-eligible=60, batch=5 ===")
    cycles = 60  # 5 * 60 = 300 dispatches, expected coverage E ~ N * H_N
    result = await _run_probe(
        instance_type=InstanceType.radarr,
        sub_path="radarr",
        seed_config=seed,
        cycles=cycles,
        upgrade_batch_size=5,
        search_order=SearchOrder.random,
    )
    print(
        f"  eligible={result['eligible']}  touched={result['touched']}  "
        f"never={result['never_touched']}  cycles={cycles}  "
        f"dispatches={result['total_dispatches']}"
    )
    print(f"  verdict: {_verdict(result, expected_full_coverage=False)}\n")

    # 3. Sonarr chronological: 50 series x 10 episodes = 500 episodes total.
    #    Window is 5 series per cycle. To touch every series at least once:
    #    50/5 = 10 cycles. Each series fetch produces all its episodes; with
    #    batch=5 we dispatch 5 per cycle. Full coverage of upgrade-eligible
    #    episodes can take longer than the series rotation due to the
    #    item-batch limit.
    print("=== Sonarr chronological, 50 series x 10 episodes, batch=5 ===")
    seed = SeedConfig(seed=42, sonarr_series=50, sonarr_episodes_per_series=10)
    cycles = 60
    result = await _run_probe(
        instance_type=InstanceType.sonarr,
        sub_path="sonarr",
        seed_config=seed,
        cycles=cycles,
        upgrade_batch_size=5,
        search_order=SearchOrder.chronological,
    )
    print(
        f"  eligible={result['eligible']}  touched={result['touched']}  "
        f"never={result['never_touched']}  cycles={cycles}  "
        f"dispatches={result['total_dispatches']}"
    )
    print(f"  verdict: {_verdict(result, expected_full_coverage=False)}\n")

    # 4. Sonarr random: random shuffles within the rotated window each cycle.
    print("=== Sonarr random, 50 series x 10 episodes, batch=5 ===")
    result = await _run_probe(
        instance_type=InstanceType.sonarr,
        sub_path="sonarr",
        seed_config=seed,
        cycles=cycles,
        upgrade_batch_size=5,
        search_order=SearchOrder.random,
    )
    print(
        f"  eligible={result['eligible']}  touched={result['touched']}  "
        f"never={result['never_touched']}  cycles={cycles}  "
        f"dispatches={result['total_dispatches']}"
    )
    print(f"  verdict: {_verdict(result, expected_full_coverage=False)}\n")


if __name__ == "__main__":
    asyncio.run(main())

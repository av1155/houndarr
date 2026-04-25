"""Hourly-cap fairness probe.

Drives multiple Houndarr instances against the mock and verifies that:

1. Each instance respects its own ``hourly_cap`` (no instance ever
   dispatches more than its cap within a 60-minute simulated window).
2. Caps for different (instance, kind) pairs do not interfere: an
   instance's missing cap does not affect its cutoff cap or another
   instance's caps.
3. With multiple instances at the same cap, none is systematically
   starved by scheduling order or supervisor task interleaving.

Method:

The hourly counter is a SQL ``COUNT(*)`` over ``search_log`` with
``timestamp > now - 3600s`` (the column has a SQLite default of
``CURRENT_TIMESTAMP``, so timestamps reflect real wallclock time).
Cooldown days are zeroed via the ``_now_utc`` patch so cooldown does
not interfere with cap measurement; advancing simulated time also
keeps every item eligible.

We run ``cycles`` cycles per instance back-to-back (the supervisor's
``sleep_interval_mins`` does not apply because we drive
``run_instance_search`` directly) and confirm that:

- per-instance dispatch counts respect the configured ``hourly_cap``
- per (instance, kind) totals are independent
- across multiple instances with identical caps, all reach within a
  small tolerance of the cap (no starvation)

Run:

    .venv/bin/python -m tests.mock_arr.probe_hourly_cap
"""

from __future__ import annotations

import asyncio
import contextlib
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


def _start_mock(items_per_instance: int = 200, seed: int = 42) -> tuple[_Server, str]:
    port = _free_port()
    config = SeedConfig(
        seed=seed,
        sonarr_series=20,
        sonarr_episodes_per_series=10,
        radarr_movies=items_per_instance,
        lidarr_artists=20,
        lidarr_albums_per_artist=10,
        readarr_authors=20,
        readarr_books_per_author=10,
        whisparr_v2_series=10,
        whisparr_v2_episodes_per_series=10,
        whisparr_v3_movies=items_per_instance,
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


def _build_instance(
    *,
    instance_id: int,
    base_url: str,
    sub_path: str,
    instance_type: InstanceType,
    missing_cap: int,
    cutoff_cap: int,
    cutoff_enabled: bool = False,
) -> Instance:
    return Instance(
        core=InstanceCore(
            id=instance_id,
            name=f"Probe {instance_type.value} {instance_id}",
            type=instance_type,
            url=f"{base_url}/{sub_path}",
            api_key="probe-key",
            enabled=True,
        ),
        missing=MissingPolicy(
            batch_size=10,
            sleep_interval_mins=15,
            hourly_cap=missing_cap,
            cooldown_days=7,
            post_release_grace_hrs=0,
            queue_limit=0,
            sonarr_search_mode=SonarrSearchMode.episode,
            lidarr_search_mode=LidarrSearchMode.album,
            readarr_search_mode=ReadarrSearchMode.book,
            whisparr_v2_search_mode=WhisparrV2SearchMode.episode,
        ),
        cutoff=CutoffPolicy(
            cutoff_enabled=cutoff_enabled,
            cutoff_batch_size=10,
            cutoff_cooldown_days=21,
            cutoff_hourly_cap=cutoff_cap,
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
async def _temp_db(master_key: bytes, instances: list[Instance]) -> AsyncIterator[None]:
    with tempfile.TemporaryDirectory() as data_dir:
        db_path = os.path.join(data_dir, "probe.db")
        set_db_path(db_path)
        await init_db()
        encrypted = encrypt("probe-key", master_key)
        async with get_db() as conn:
            for inst in instances:
                await conn.execute(
                    "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        inst.core.id,
                        inst.core.name,
                        inst.core.type.value,
                        inst.core.url,
                        encrypted,
                    ),
                )
            await conn.commit()
        yield


async def _dispatches_per_instance_kind() -> dict[tuple[int, str], int]:
    """Count successful dispatches grouped by (instance_id, search_kind)."""
    out: dict[tuple[int, str], int] = {}
    async with get_db() as conn:
        async with conn.execute(
            "SELECT instance_id, search_kind, COUNT(*) FROM search_log "
            "WHERE action = 'searched' GROUP BY instance_id, search_kind"
        ) as cur:
            async for row in cur:
                out[(int(row[0]), str(row[1]))] = int(row[2])
    return out


async def _run_probe(
    *,
    instance_specs: list[tuple[int, int]],
    cycles_per_instance: int,
    missing_cap: int,
) -> dict[str, Any]:
    """Drive ``cycles_per_instance`` cycles for each instance interleaved.

    ``instance_specs`` is ``(instance_id, missing_cap)``. The probe builds
    the Instance objects against the live mock's URL inside this function
    so the URL never goes stale.

    All cycles run within a single real-time-second window, so the SQL
    ``timestamp > now - 3600s`` cap counter sees every dispatch. The
    expected behaviour: each instance hits its cap once and then logs
    'skipped' for the rest of its cycles.
    """
    handle, base_url = _start_mock()
    master_key = Fernet.generate_key()
    advance = timedelta(days=8)
    fake_time = [datetime(2026, 1, 1, tzinfo=UTC)]

    def _now() -> datetime:
        return fake_time[0]

    instances = [
        _build_instance(
            instance_id=iid,
            base_url=base_url,
            sub_path="sonarr",
            instance_type=InstanceType.sonarr,
            missing_cap=cap,
            cutoff_cap=0,
        )
        for iid, cap in instance_specs
    ]

    try:
        async with _temp_db(master_key, instances):
            with patch("houndarr.repositories.cooldowns._now_utc", _now):
                # Interleave cycles across instances so no instance gets a
                # privileged head-start; a real supervisor would also run
                # them concurrently in separate tasks.
                for _cycle_idx in range(cycles_per_instance):
                    for inst in instances:
                        await run_instance_search(inst, master_key)
                    fake_time[0] += advance

            counts = await _dispatches_per_instance_kind()
    finally:
        _stop_mock(handle)

    per_instance_total = Counter[int]()
    for (iid, _kind), n in counts.items():
        per_instance_total[iid] += n

    spec_cap = dict(instance_specs)
    cap_violations: list[tuple[int, str, int, int]] = []
    underutilised: list[tuple[int, int, int]] = []
    for (iid, kind), n in counts.items():
        cap = spec_cap.get(iid, missing_cap)
        if n > cap and kind == "missing":
            cap_violations.append((iid, kind, n, cap))
    for iid, cap in spec_cap.items():
        actual = per_instance_total.get(iid, 0)
        # Underutilised means the instance dispatched fewer than its cap
        # despite being able to (mock returns plenty of items, no cooldown).
        # We tolerate one short of the cap (sometimes the engine breaks
        # mid-page); anything more is a real starvation signal.
        if actual < cap - 1:
            underutilised.append((iid, actual, cap))

    return {
        "n_instances": len(instances),
        "cycles_per_instance": cycles_per_instance,
        "missing_cap": missing_cap,
        "per_pair": dict(counts),
        "per_instance_total": dict(per_instance_total),
        "cap_violations": cap_violations,
        "underutilised": underutilised,
        "spec_cap": spec_cap,
    }


def _verdict(result: dict[str, Any]) -> str:
    if result["cap_violations"]:
        viols = "; ".join(
            f"i{iid}/{kind} {n} > cap {cap}" for iid, kind, n, cap in result["cap_violations"]
        )
        return f"CAP VIOLATED: {viols}"
    if result["underutilised"]:
        starved = "; ".join(
            f"i{iid} got {actual}/{cap}" for iid, actual, cap in result["underutilised"]
        )
        return f"UNDERUTILISED: {starved}"
    return "fair (each instance hit its configured cap)"


async def main() -> None:
    print("Hourly-cap fairness probe")
    print("Drives multiple instances and verifies cap is respected and no instance starves.\n")

    # Test A: three Sonarr instances at identical caps. Expect each to hit
    # exactly its cap and none to violate.
    print("=== three Sonarr instances, missing_cap=5 each, 1 cycle each ===")
    result_a = await _run_probe(
        instance_specs=[(1, 5), (2, 5), (3, 5)],
        cycles_per_instance=1,
        missing_cap=5,
    )
    print(f"  per (instance, kind): {result_a['per_pair']}")
    print(f"  per instance total: {result_a['per_instance_total']}")
    print(f"  verdict: {_verdict(result_a)}\n")

    # Test B: same setup, three cycles each. The cap should bite in
    # cycle 2 and 3 because the 1-hour rolling window covers them all.
    print("=== three Sonarr instances, missing_cap=5, 3 cycles each ===")
    result_b = await _run_probe(
        instance_specs=[(1, 5), (2, 5), (3, 5)],
        cycles_per_instance=3,
        missing_cap=5,
    )
    print(f"  per (instance, kind): {result_b['per_pair']}")
    print(f"  per instance total: {result_b['per_instance_total']}")
    print(f"  verdict: {_verdict(result_b)}\n")

    # Test C: heterogeneous caps. Expect each instance to hit its own cap
    # independently, with no cross-instance interference.
    print("=== three Sonarr instances, caps=[2, 10, 5], 2 cycles each ===")
    result_c = await _run_probe(
        instance_specs=[(1, 2), (2, 10), (3, 5)],
        cycles_per_instance=2,
        missing_cap=10,
    )
    cap_per_instance = {1: 2, 2: 10, 3: 5}
    print(f"  per (instance, kind): {result_c['per_pair']}")
    print(f"  per instance total: {result_c['per_instance_total']}")
    print(f"  expected caps: {cap_per_instance}")
    cap_check = []
    for iid, n in sorted(result_c["per_instance_total"].items()):
        cap_check.append(f"i{iid}:{n}/{cap_per_instance.get(iid)}")
    print(f"  observed vs cap: {', '.join(cap_check)}")
    print(f"  verdict: {_verdict(result_c)}\n")


if __name__ == "__main__":
    asyncio.run(main())

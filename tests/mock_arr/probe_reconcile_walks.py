"""Reconcile walk probe (#897).

A snapshot refresh walks each ``/wanted`` list in pages of 250 and deletes
every cooldown whose item the walk did not see.  When the list changes
between two page reads (an import, a new release) or the database orders
tied rows differently per page, the walk can miss a still-wanted item and
end on a short page all the same.  This probe measures how many live
cooldowns that deletes, and what reading such walks as churned costs.

It drives the real ``Supervisor._refresh_one_snapshot`` against the mock
over HTTP, arms the mock's ``/wanted`` faults, and observes only what an
operator could: cooldown rows, the mock's fired-fault log and WARNING
records.  Before every refresh each wanted item gets a cooldown, plus one
sentinel row per list for an item that does not exist, so a deleted
sentinel means that list's pass reconciled.  The probe runs unchanged
before and after the fix, and reports each trial against both models.

Deterministic mode arms one fault per trial for every op, list and page
the walk reaches, on Sonarr and Lidarr, with a fresh partition and a fresh
supervisor per trial.  The pre-fix model: every pass reconciles, and the
item each leave, leave_join, tie_split or leave_after_records slid past
loses its cooldown.  The post-fix model: a pass whose fault changed the
list's total does not reconcile and deletes nothing, a tie_split pass
reconciles after a second walk at another page size and keeps every live
cooldown, and leave_join (one item leaves and one joins in the same gap,
the total never moves) still loses one, the known residual.

Poisson mode is the cost side.  One supervisor refreshes Sonarr many
times; before each refresh every page request of the missing walk gets a
fault with probability ``1 - exp(-m / pages)``, so a walk sees one or more
changes with probability ``1 - exp(-m)``.  An event is an import of a read
item (``leave``) or an unread one (``leave_unread``) in proportion to the
offset, or one time in five a new release (``join``); page 1 can only race
its own count (``leave_after_records``).  The post-fix model predicts the
pass skips exactly the eventful walks, so reconciles come every
``exp(m)`` refreshes on average, and one WARNING per run of six or more.

Run:

    .venv/bin/python -m tests.mock_arr.probe_reconcile_walks
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import os
import random
import socket
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn
from cryptography.fernet import Fernet

from houndarr.crypto import encrypt
from houndarr.database import close_all_pools, get_db, init_db, set_db_path
from houndarr.engine.adapters._common import _RECONCILE_PAGE_SIZE
from houndarr.engine.supervisor import Supervisor
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
    SonarrSearchMode,
    TagFilterPolicy,
    UpgradePolicy,
    WhisparrV2SearchMode,
)
from tests.mock_arr.server import SeedConfig, create_app

_PAGE = _RECONCILE_PAGE_SIZE
_KINDS = ("missing", "cutoff")
_SENTINEL = {"missing": 9_999_001, "cutoff": 9_999_002}
_SKIPPING_OPS = frozenset({"leave", "leave_join", "tie_split", "leave_after_records"})
_TOTAL_MOVING_OPS = frozenset(
    {"leave", "leave_unread", "join", "join_before", "leave_after_records"}
)
_BOUNDARY_OPS = ("leave", "leave_unread", "join", "join_before", "leave_join", "tie_split")


@dataclass(frozen=True, slots=True)
class _App:
    instance_id: int
    name: str
    itype: InstanceType
    item_type: str
    api: str
    sort_key: str


_APPS = (
    _App(1, "sonarr", InstanceType.sonarr, "episode", "v3", "airDateUtc"),
    _App(3, "lidarr", InstanceType.lidarr, "album", "v1", "releaseDate"),
)

# 1300 leaves per app: 650 missing (three pages) and 260 cutoff (two).
_SEED = SeedConfig(
    shuffle_dates=True,
    sonarr_series=130,
    sonarr_episodes_per_series=10,
    lidarr_artists=130,
    lidarr_albums_per_artist=10,
)


@dataclass(slots=True)
class _Mock:
    server: uvicorn.Server
    thread: threading.Thread
    url: str


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_mock() -> _Mock:
    port = _free_port()
    config = uvicorn.Config(
        app=create_app(_SEED), host="127.0.0.1", port=port, log_level="error", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock failed to start")
    return _Mock(server=server, thread=thread, url=f"http://127.0.0.1:{port}")


def _instance(app: _App, base_url: str) -> Instance:
    return Instance(
        core=InstanceCore(
            id=app.instance_id,
            name=f"Probe {app.name}",
            type=app.itype,
            url=f"{base_url}/{app.name}",
            api_key="probe-key",
            enabled=True,
        ),
        missing=MissingPolicy(
            batch_size=1,
            sleep_interval_mins=15,
            hourly_cap=1,
            cooldown_days=7,
            post_release_grace_hrs=0,
            queue_limit=0,
            sonarr_search_mode=SonarrSearchMode.episode,
            lidarr_search_mode=LidarrSearchMode.album,
            readarr_search_mode=ReadarrSearchMode.book,
            whisparr_v2_search_mode=WhisparrV2SearchMode.episode,
        ),
        cutoff=CutoffPolicy(cutoff_enabled=True),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(),
        tag_filter=TagFilterPolicy(),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z"
        ),
    )


@contextlib.asynccontextmanager
async def _temp_db(master_key: bytes) -> AsyncIterator[None]:
    with tempfile.TemporaryDirectory() as data_dir:
        set_db_path(os.path.join(data_dir, "probe.db"))
        try:
            await init_db()
            encrypted = encrypt("probe-key", master_key)
            async with get_db() as conn:
                await conn.executemany(
                    "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [
                        (a.instance_id, f"Probe {a.name}", a.itype.value, "http://x", encrypted)
                        for a in _APPS
                    ],
                )
                await conn.commit()
            yield
        finally:
            await close_all_pools()


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def warnings(self) -> int:
        return sum(
            1
            for r in self.records
            if r.levelno == logging.WARNING and "incomplete" in r.getMessage()
        )


@contextlib.contextmanager
def _capture() -> Iterator[_Records]:
    handler = _Records()
    logger = logging.getLogger("houndarr.engine.supervisor")
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


async def _wanted(http: httpx.AsyncClient, mock: _Mock, app: _App, kind: str) -> list[int]:
    resp = await http.get(
        f"{mock.url}/{app.name}/api/{app.api}/wanted/{kind}",
        params={"pageSize": 2000, "sortKey": app.sort_key, "sortDirection": "ascending"},
    )
    resp.raise_for_status()
    return [record["id"] for record in resp.json()["records"]]


async def _seed(app: _App, wanted: dict[str, list[int]], *, wipe: bool) -> None:
    rows = [(item_id, kind) for kind in _KINDS for item_id in [*wanted[kind], _SENTINEL[kind]]]
    present = set() if wipe else await _cooldowns(app)
    async with get_db() as conn:
        if wipe:
            await conn.execute("DELETE FROM cooldowns WHERE instance_id = ?", (app.instance_id,))
        await conn.executemany(
            "INSERT INTO cooldowns (instance_id, item_id, item_type, search_kind, searched_at)"
            " VALUES (?, ?, ?, ?, datetime('now'))",
            [(app.instance_id, i, app.item_type, k) for i, k in rows if (i, k) not in present],
        )
        await conn.commit()


async def _cooldowns(app: _App) -> set[tuple[int, str]]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT item_id, search_kind FROM cooldowns WHERE instance_id = ?",
            (app.instance_id,),
        ) as cur:
            return {(int(r[0]), str(r[1])) for r in await cur.fetchall()}


@dataclass(slots=True)
class _Outcome:
    fired: list[dict[str, Any]]
    still_wanted: dict[str, set[int]]
    wrongly_reaped: dict[str, int]
    reconciled: dict[str, bool]
    stale_kept: int
    warnings: int

    def predict(self, kind: str, model: str) -> tuple[int, bool]:
        """Return (wrongly reaped, reconciled) for ``kind`` under ``model``.

        A skipped item that a later fault in the same walk removed really
        left, so only skipped items still wanted afterwards count.
        """
        events = [e for e in self.fired if e["kind"] == kind]
        skips = [
            e
            for e in events
            if e["op"] in _SKIPPING_OPS and e["skipped"] in self.still_wanted[kind]
        ]
        if model == "pre":
            return len(skips), True
        if any(e["op"] in _TOTAL_MOVING_OPS for e in events):
            return 0, False
        return sum(1 for e in skips if e["op"] == "leave_join"), True


async def _refresh(
    http: httpx.AsyncClient,
    mock: _Mock,
    app: _App,
    supervisor: Supervisor,
    faults: list[dict[str, Any]],
    *,
    wipe: bool,
) -> _Outcome:
    before = {kind: await _wanted(http, mock, app, kind) for kind in _KINDS}
    await _seed(app, before, wipe=wipe)
    await http.put(f"{mock.url}/__faults__/{app.name}", json={"faults": faults})
    fired_before = len((await http.get(f"{mock.url}/__faults__/{app.name}")).json()["fired"])
    with _capture() as records:
        await supervisor._refresh_one_snapshot(_instance(app, mock.url))
    fired = (await http.get(f"{mock.url}/__faults__/{app.name}")).json()["fired"][fired_before:]
    await http.put(f"{mock.url}/__faults__/{app.name}", json={"faults": []})
    after = {kind: set(await _wanted(http, mock, app, kind)) for kind in _KINDS}
    rows = await _cooldowns(app)
    wrongly = {
        kind: sum(1 for i in before[kind] if i in after[kind] and (i, kind) not in rows)
        for kind in _KINDS
    }
    reconciled = {kind: (_SENTINEL[kind], kind) not in rows for kind in _KINDS}
    stale = sum(
        1 for i, kind in rows if i != _SENTINEL.get(kind) and i not in after.get(kind, set())
    )
    return _Outcome(fired, after, wrongly, reconciled, stale, records.warnings())


@dataclass(slots=True)
class _Tally:
    trials: int = 0
    matches: dict[str, int] = field(default_factory=lambda: {"pre": 0, "post": 0})
    reaped: dict[str, int] = field(default_factory=lambda: {"pre": 0, "post": 0})
    measured_reaped: int = 0
    warnings: int = 0


def _trials(app: _App, sizes: dict[str, int]) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for kind in _KINDS:
        last = sizes[kind] // _PAGE + 1
        out.extend((kind, op, page) for op in _BOUNDARY_OPS for page in range(2, last + 1))
        out.extend((kind, "leave_after_records", page) for page in range(1, last + 1))
    return out


async def _deterministic(http: httpx.AsyncClient, mock: _Mock, master_key: bytes) -> _Tally:
    tally = _Tally()
    print(
        f"{'app':<7} {'list':<8} {'op':<20} {'pg':>2}  {'fired':>5}  "
        f"{'reaped':>6} {'pre':>4} {'post':>4}  {'recon':>5} {'pre':>5} {'post':>5}"
    )
    for app in _APPS:
        await http.post(f"{mock.url}/__reset__/{app.name}")
        sizes = {kind: len(await _wanted(http, mock, app, kind)) for kind in _KINDS}
        control = await _refresh(http, mock, app, Supervisor(master_key=master_key), [], wipe=True)
        assert all(control.reconciled.values()) and not any(control.wrongly_reaped.values())
        for kind, op, page in _trials(app, sizes):
            await http.post(f"{mock.url}/__reset__/{app.name}")
            fault = {"kind": kind, "page": page, "op": op, "page_size": _PAGE}
            outcome = await _refresh(
                http, mock, app, Supervisor(master_key=master_key), [fault], wipe=True
            )
            tally.trials += 1
            tally.warnings += outcome.warnings
            measured = (outcome.wrongly_reaped[kind], outcome.reconciled[kind])
            tally.measured_reaped += measured[0]
            other = "cutoff" if kind == "missing" else "missing"
            clean_other = outcome.reconciled[other] and not outcome.wrongly_reaped[other]
            predictions = {model: outcome.predict(kind, model) for model in ("pre", "post")}
            for model, predicted in predictions.items():
                tally.reaped[model] += predicted[0]
                if predicted == measured and clean_other and not outcome.warnings:
                    tally.matches[model] += 1
            print(
                f"{app.name:<7} {kind:<8} {op:<20} {page:>2}  {len(outcome.fired):>5}  "
                f"{measured[0]:>6} {predictions['pre'][0]:>4} {predictions['post'][0]:>4}  "
                f"{measured[1]!s:>5} {predictions['pre'][1]!s:>5} {predictions['post'][1]!s:>5}"
            )
    return tally


def _runs(flags: list[bool]) -> list[int]:
    runs, length = [], 0
    for flag in flags:
        if flag:
            length += 1
        elif length:
            runs.append(length)
            length = 0
    if length:
        runs.append(length)
    return runs


def _poisson_faults(rng: random.Random, size: int, m: float) -> list[dict[str, Any]]:
    pages = size // _PAGE + 1
    q = 1 - math.exp(-m / pages)
    faults: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        if rng.random() >= q:
            continue
        if page == 1:
            op = "leave_after_records"
        elif rng.random() < 0.2:
            op = "join"
        else:
            offset = (page - 1) * _PAGE
            op = "leave" if rng.random() < offset / max(size, 1) else "leave_unread"
        faults.append({"kind": "missing", "page": page, "op": op, "page_size": _PAGE})
    return faults


async def _poisson(
    http: httpx.AsyncClient, mock: _Mock, master_key: bytes, refreshes: int, seed: int
) -> None:
    app = _APPS[0]
    print(
        f"\nSonarr missing, {refreshes} refreshes per m.  armed: walks given a fault "
        "(expect 1-e^-m); fired: walks a fault changed; skipped: refreshes whose missing "
        "pass kept its rows; skip=fire: refreshes where the two agree (post-fix model: all)."
    )
    print(
        f"{'m':>5}  {'armed':>6} {'1-e^-m':>6}  {'fired':>6} {'skipped':>7} {'skip=fire':>9}  "
        f"{'refr/recon':>10} {'1/(1-f)':>7}  {'warn':>4} {'runs>=6':>7}  "
        f"{'reaped':>6} {'pre':>4} {'post':>4}  {'stale':>5}"
    )
    for m in (0.1, 0.5, 1.0, 2.0):
        rng = random.Random(f"{seed}:{m}")
        await http.post(f"{mock.url}/__reset__/{app.name}")
        supervisor = Supervisor(master_key=master_key)
        armed = 0
        fired: list[bool] = []
        skipped: list[bool] = []
        warnings = reaped = predicted_pre = predicted_post = stale = 0
        for index in range(refreshes):
            size = len(await _wanted(http, mock, app, "missing"))
            faults = _poisson_faults(rng, size, m)
            armed += bool(faults)
            outcome = await _refresh(http, mock, app, supervisor, faults, wipe=index == 0)
            fired.append(bool(outcome.fired))
            skipped.append(not outcome.reconciled["missing"])
            warnings += outcome.warnings
            reaped += outcome.wrongly_reaped["missing"]
            predicted_pre += outcome.predict("missing", "pre")[0]
            predicted_post += outcome.predict("missing", "post")[0]
            stale += outcome.stale_kept
        reconciles = refreshes - sum(skipped)
        fired_share = sum(fired) / refreshes
        long_runs = sum(1 for run in _runs(skipped) if run >= 6)
        agree = sum(1 for f, s in zip(fired, skipped, strict=True) if f == s)
        print(
            f"{m:>5.1f}  {armed / refreshes:>6.3f} {1 - math.exp(-m):>6.3f}  "
            f"{fired_share:>6.3f} {sum(skipped) / refreshes:>7.3f} {agree:>9}  "
            f"{refreshes / max(reconciles, 1):>10.2f} {1 / max(1 - fired_share, 1e-9):>7.2f}  "
            f"{warnings:>4} {long_runs:>7}  "
            f"{reaped:>6} {predicted_pre:>4} {predicted_post:>4}  {stale / refreshes:>5.1f}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("deterministic", "poisson", "both"), default="both")
    parser.add_argument("--refreshes", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    mock = _start_mock()
    master_key = Fernet.generate_key()
    try:
        async with _temp_db(master_key), httpx.AsyncClient(timeout=30) as http:
            if args.mode in ("deterministic", "both"):
                tally = await _deterministic(http, mock, master_key)
                print(
                    f"\ntrials={tally.trials}  live cooldowns deleted={tally.measured_reaped}  "
                    f"(pre-fix model {tally.reaped['pre']}, post-fix model "
                    f"{tally.reaped['post']})  incomplete WARNINGs={tally.warnings}"
                )
                print(
                    f"trials matching the pre-fix model: {tally.matches['pre']}/{tally.trials}; "
                    f"post-fix model: {tally.matches['post']}/{tally.trials}"
                )
            if args.mode in ("poisson", "both"):
                await _poisson(http, mock, master_key, args.refreshes, args.seed)
    finally:
        mock.server.should_exit = True
        mock.thread.join(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())

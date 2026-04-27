"""Seed a scratch Houndarr data directory with demo content for marketing captures.

Two modes:

* ``populated`` (default): seven instances covering all six supported types
  (one disabled Whisparr v3 for the mixed-state look), roughly 120 cooldowns
  staggered across 2-3 days so the Cooldown Schedule panel has a real
  spread, matching search_log rows so labels resolve, 42 historical
  searches on the disabled instance to exercise the muted treatment, plus
  eight realistic recent cycles (see ``demo_cycles.py``) mixing searched,
  skipped with every engine-emitted reason, plus an error + an info row,
  across missing/cutoff/upgrade passes and scheduled/run_now triggers.
* ``empty``: admin account only, no instances. Used to capture the
  empty-state dashboard screenshot.

Title pools live in ``demo_titles/*.json`` so the fictional library can be
refreshed without touching Python. The script is idempotent against a
given ``--data-dir``: running it twice over the same dir replaces the
seeded content; the Fernet master key is reused when present so the
admin credentials survive.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiosqlite  # noqa: E402
import bcrypt  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from demo_cycles import build_realistic_cycles  # noqa: E402

from houndarr.bootstrap import bootstrap_non_web  # noqa: E402

_TITLES_DIR = Path(__file__).resolve().parent / "demo_titles"


def _load_titles() -> dict[str, list[tuple[int, str]]]:
    """Load title pools from the adjacent demo_titles directory."""
    tv = json.loads((_TITLES_DIR / "tv.json").read_text())
    movies = json.loads((_TITLES_DIR / "movies.json").read_text())
    albums = json.loads((_TITLES_DIR / "albums.json").read_text())
    books = json.loads((_TITLES_DIR / "books.json").read_text())
    return {
        "sonarr": [(int(i), s) for i, s in tv["sonarr"]],
        "sonarr_4k": [(int(i), s) for i, s in tv["sonarr_4k"]],
        "radarr": [(int(i), s) for i, s in movies["radarr"]],
        "radarr_4k": [(int(i), s) for i, s in movies["radarr_4k"]],
        "lidarr": [(int(i), s) for i, s in albums["lidarr"]],
        "readarr": [(int(i), s) for i, s in books["readarr"]],
    }


async def _seed_admin(conn: aiosqlite.Connection, password: str) -> None:
    """Create or replace the admin credential and suppress the changelog modal."""
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await conn.executemany(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [
            ("username", "admin"),
            ("password_hash", password_hash),
            ("session_secret", secrets.token_urlsafe(32)),
            ("changelog_last_seen_version", "1.9.0"),
            ("changelog_popups_disabled", "true"),
        ],
    )


async def _wipe_demo_tables(conn: aiosqlite.Connection) -> None:
    """Remove prior demo content so the script is re-runnable."""
    await conn.execute("DELETE FROM search_log")
    await conn.execute("DELETE FROM cooldowns")
    await conn.execute("DELETE FROM instances")


def _instances_spec() -> list[tuple[Any, ...]]:
    """Fixed demo instance set. Six rows covering five active arr types
    plus a disabled Whisparr v3 for the mixed-state look.

    Cutoff is enabled on every active instance and upgrade is enabled on
    the TV + movie instances so the dashboard + logs surfaces exercise
    every pass kind (missing, cutoff, upgrade) a real user with
    aggressive settings would see.
    """
    # Fields: id, name, type, url, enabled, cutoff_enabled, upgrade_enabled,
    #         sleep_interval_mins, monitored_total, unreleased_count.
    return [
        (1, "Sonarr", "sonarr", "http://sonarr:8989", 1, 1, 1, 30, 120, 8),
        (2, "Radarr", "radarr", "http://radarr:7878", 1, 1, 1, 30, 180, 12),
        (3, "Radarr 4K", "radarr", "http://radarr-4k:7878", 1, 1, 1, 60, 95, 6),
        (4, "Lidarr", "lidarr", "http://lidarr:8686", 1, 1, 1, 30, 240, 4),
        (5, "Readarr", "readarr", "http://readarr:8787", 1, 1, 1, 60, 48, 2),
        (6, "Whisparr v3", "whisparr_v3", "http://whisparr:6969", 0, 0, 0, 30, 0, 0),
    ]


async def _seed_instances(conn: aiosqlite.Connection, enc_key: str, now_iso: str) -> None:
    """Write the seven demo instances with matching snapshot columns."""
    for (
        iid,
        name,
        itype,
        url,
        enabled,
        cutoff,
        upgrade,
        sleep_m,
        mono,
        unrel,
    ) in _instances_spec():
        await conn.execute(
            """
            INSERT INTO instances
                (id, name, type, url, encrypted_api_key,
                 batch_size, sleep_interval_mins, hourly_cap,
                 cooldown_days, post_release_grace_hrs, queue_limit,
                 cutoff_enabled, upgrade_enabled,
                 enabled, monitored_total, unreleased_count,
                 snapshot_refreshed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?,
                    2, ?, 4,
                    14, 6, 0,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?)
            """,
            (
                iid,
                name,
                itype,
                url,
                enc_key,
                sleep_m,
                cutoff,
                upgrade,
                enabled,
                mono,
                unrel,
                now_iso,
                now_iso,
                now_iso,
            ),
        )


def _stagger_cooldowns(
    instance_id: int,
    pool: list[tuple[int, str]],
    count: int,
    item_type: str,
    search_kind: str,
    oldest_hours: float,
    newest_hours: float,
    now: datetime,
) -> list[tuple[int, int, str, str, str]]:
    """Evenly space ``count`` rows between ``oldest_hours`` and ``newest_hours`` ago.

    Returns 5-tuples ``(instance_id, item_id, item_type, searched_at,
    search_kind)``.  The fifth element is the pass kind that booked
    the cooldown.  The ``cooldowns`` table tracks search_kind directly
    (schema v14+), so the INSERT below carries it through; the
    matching ``search_log`` row uses the same kind so the
    dashboard-side breakdown queries agree with the cooldown row.
    """
    picks = pool[:count]
    if len(picks) < 2:
        steps = [oldest_hours] * len(picks)
    else:
        step = (oldest_hours - newest_hours) / (len(picks) - 1)
        steps = [oldest_hours - step * i for i in range(len(picks))]
    rows: list[tuple[int, int, str, str, str]] = []
    for (item_id, _label), hours in zip(picks, steps, strict=False):
        ts = now - timedelta(hours=hours)
        rows.append((instance_id, item_id, item_type, ts.isoformat(), search_kind))
    return rows


def _distribute_cooldowns(
    instance_id: int,
    pool: list[tuple[int, str]],
    item_type: str,
    missing_count: int,
    cutoff_count: int,
    upgrade_count: int,
    now: datetime,
) -> list[tuple[int, int, str, str, str]]:
    """Split a pool across missing / cutoff / upgrade cooldowns.

    Each kind takes a contiguous slice of the pool so no ``item_id``
    repeats within an instance (the ``cooldowns`` table has
    ``UNIQUE(instance_id, item_id, item_type)``). Time ranges differ
    by kind so the Cooldown Schedule panel renders a realistic spread:
    missing rows span 67h -> 3h ago (14d cooldown left 11d 7h to 13d
    23h), cutoff rows span 63h -> 5h (21d cooldown, further out), and
    upgrade rows span 60h -> 10h (90d cooldown, furthest out).
    """
    rows: list[tuple[int, int, str, str, str]] = []
    cursor = 0
    if missing_count:
        rows += _stagger_cooldowns(
            instance_id,
            pool[cursor : cursor + missing_count],
            missing_count,
            item_type,
            "missing",
            67,
            3,
            now,
        )
        cursor += missing_count
    if cutoff_count:
        rows += _stagger_cooldowns(
            instance_id,
            pool[cursor : cursor + cutoff_count],
            cutoff_count,
            item_type,
            "cutoff",
            63,
            5,
            now,
        )
        cursor += cutoff_count
    if upgrade_count:
        rows += _stagger_cooldowns(
            instance_id,
            pool[cursor : cursor + upgrade_count],
            upgrade_count,
            item_type,
            "upgrade",
            60,
            10,
            now,
        )
    return rows


def _pool_label(pools: dict[str, list[tuple[int, str]]], item_id: int) -> str:
    """Return the display label for ``item_id`` from any configured pool."""
    for pool in pools.values():
        for pid, lab in pool:
            if pid == item_id:
                return lab
    return f"Item {item_id}"


async def _seed_cooldowns_and_logs(
    conn: aiosqlite.Connection,
    pools: dict[str, list[tuple[int, str]]],
    now: datetime,
) -> tuple[int, int]:
    """Seed cooldowns + matching search_log rows + recent hunts.

    Per-instance cooldown counts are split across missing / cutoff /
    upgrade kinds so the dashboard's library-health bar shows amber
    (cutoff) and violet (upgrade) segments prominently instead of one
    dominant cyan cooldown bar. Mix matches what a real Houndarr
    install with cutoff + upgrade passes enabled would accumulate
    over a few weeks: cutoff roughly 80% of missing (quality-cutoff
    is the second most common reason items need re-searching), and
    upgrade roughly 20-25% of missing (upgrades have a 90-day
    cooldown so items rotate through slower).
    """
    # (instance_id, pool_key, item_type, missing, cutoff, upgrade).
    # Pool sizes (25/30/20/20/16) cap the per-instance totals; every
    # active instance gets at least one cooldown of each kind so the
    # dashboard's library-health bar paints all five segments and the
    # cooldown-breakdown row on every card shows missing + cutoff +
    # upgrade segments.
    splits: list[tuple[int, str, str, int, int, int]] = [
        (1, "sonarr", "episode", 11, 10, 4),
        (2, "radarr", "movie", 13, 12, 5),
        (3, "radarr_4k", "movie", 9, 8, 3),
        (4, "lidarr", "album", 10, 7, 3),
        (5, "readarr", "book", 8, 5, 3),
    ]
    cooldown_rows: list[tuple[int, int, str, str, str]] = []
    for iid, pool_key, item_type, miss, cut, upg in splits:
        cooldown_rows += _distribute_cooldowns(iid, pools[pool_key], item_type, miss, cut, upg, now)

    await conn.executemany(
        """
        INSERT INTO cooldowns (instance_id, item_id, item_type, searched_at, search_kind)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(r[0], r[1], r[2], r[3], r[4]) for r in cooldown_rows],
    )

    log_rows: list[tuple[Any, ...]] = []
    # Replay every cooldown as a searched row so labels resolve AND the
    # dashboard's cooldown-breakdown JOIN sees each row's real pass kind.
    for iid, item_id, item_type, ts_iso, search_kind in cooldown_rows:
        log_rows.append(
            (
                iid,
                item_id,
                item_type,
                search_kind,
                uuid.uuid4().hex[:12],
                "scheduled",
                _pool_label(pools, item_id),
                "searched",
                None,
                "dispatched",
                ts_iso,
            )
        )

    # Historical searches for the disabled Whisparr v3 instance: 42 rows
    # between 5 and 20 days ago so its card shows SEARCHED 42 (muted).
    for i in range(42):
        days_ago = 5 + (i * 15 / 41)
        ts = now - timedelta(days=days_ago)
        log_rows.append(
            (
                6,
                80000 + i,
                "whisparr_v3_movie",
                "missing",
                uuid.uuid4().hex[:12],
                "scheduled",
                f"Scene {i + 1}",
                "searched",
                None,
                "dispatched",
                ts.isoformat(),
            )
        )

    # Realistic recent cycles: mix of searched / skipped / error / info
    # across every pass kind.  Also populates the dashboard's Recent
    # Hunts strip with the searched rows inside the last 6h.
    log_rows.extend(build_realistic_cycles(pools, now))

    await conn.executemany(
        """
        INSERT INTO search_log
            (instance_id, item_id, item_type, search_kind, cycle_id,
             cycle_trigger, item_label, action, reason, message, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        log_rows,
    )
    return len(cooldown_rows), len(log_rows)


async def _run(
    mode: str, data_dir: Path, db_path: Path, master_key: bytes, admin_password: str
) -> None:
    """Orchestrate mode-specific seeding against an already-bootstrapped DB."""
    fernet = Fernet(master_key)
    enc_key = fernet.encrypt(b"demo-api-key-for-marketing").decode()

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await _wipe_demo_tables(conn)
        await _seed_admin(conn, admin_password)

        if mode == "empty":
            await conn.commit()
            print(f"[seed] empty-mode: admin only at {data_dir}")
            return

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        await _seed_instances(conn, enc_key, now_iso)
        pools = _load_titles()
        cd_count, log_count = await _seed_cooldowns_and_logs(conn, pools, now)
        await conn.commit()
        print(
            f"[seed] populated: 6 instances, {cd_count} cooldowns, "
            f"{log_count} log rows at {data_dir}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mode",
        choices=["populated", "empty"],
        default="populated",
        help="populated (7 instances + data) or empty (admin only).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "marketing-data",
        help="Directory for houndarr.db + houndarr.masterkey. Defaults to ./marketing-data.",
    )
    parser.add_argument(
        "--admin-password",
        default="E2EShot1!",
        help="Admin password the capture script will use to log in.",
    )
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    # bootstrap_non_web is the shared sync composition: create data_dir,
    # ensure the Fernet master key, and run init_db to v13. It must be
    # called before asyncio.run() because it spins up its own event loop
    # internally via asyncio.run(init_db()).
    _settings, db_path, master_key = bootstrap_non_web(data_dir=str(data_dir))
    asyncio.run(_run(args.mode, data_dir, db_path, master_key, args.admin_password))


if __name__ == "__main__":
    main()

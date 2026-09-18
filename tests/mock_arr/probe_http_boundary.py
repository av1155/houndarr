"""Drive every app through a real HTTP boundary and read the mock's own record.

The engine suite asserts against respx, which replays what the test author
believed the \\*arr would send.  This boots the seeded mock server, runs the
production ``run_instance_search`` against it over real HTTP, and then checks
the mock's own ledgers: which search commands it received (``/__commands__``),
which wanted pages were fetched (``/__page_log__``), and how many times the
download queue was read (``/__queue__``).

Run with::

    .venv/bin/python -m tests.mock_arr.probe_http_boundary
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import tempfile
from pathlib import Path
from typing import Any

import httpx
import uvicorn

# Each entry maps the mock's app prefix to the instance field that selects
# context mode, or None for the two movie apps that only search item by item.
APPS: dict[str, tuple[str, str] | None] = {
    "sonarr": ("sonarr_search_mode", "season_context"),
    "radarr": None,
    "lidarr": ("lidarr_search_mode", "artist_context"),
    "readarr": ("readarr_search_mode", "author_context"),
    "whisparr_v2": ("whisparr_v2_search_mode", "season_context"),
    "whisparr_v3": None,
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.asynccontextmanager
async def _mock_server(port: int):
    """Serve the seeded multi-app mock for the duration of the block."""
    from tests.mock_arr.server import SeedConfig, create_app

    app = create_app(SeedConfig(seed=42))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        await task


async def _run_app(base: str, app: str, context: bool) -> dict[str, Any]:
    """Run one cycle for *app* and return what the mock actually recorded."""
    from houndarr.engine.search_loop import run_instance_search
    from houndarr.services import instances as inst_mod
    from houndarr.services.instances import InstanceType
    from tests.test_engine.conftest import make_instance

    mode_enums = {
        "sonarr_search_mode": inst_mod.SonarrSearchMode,
        "lidarr_search_mode": inst_mod.LidarrSearchMode,
        "readarr_search_mode": inst_mod.ReadarrSearchMode,
        "whisparr_v2_search_mode": inst_mod.WhisparrV2SearchMode,
    }
    overrides: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType(app),
        "url": f"{base}/{app}",
        "batch_size": 3,
        "hourly_cap": 50,
        "cooldown_days": 7,
        "post_release_grace_hrs": 0,
    }
    spec = APPS[app]
    if context and spec is not None:
        field, value = spec
        overrides[field] = getattr(mode_enums[field], value)

    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{base}/__reset__/{app}")

    searched = await run_instance_search(make_instance(**overrides), b"0" * 32)

    async with httpx.AsyncClient(timeout=20) as c:
        cmds = (await c.get(f"{base}/__commands__/{app}")).json()["commands"]
        pages = (await c.get(f"{base}/__page_log__/{app}")).json()["entries"]
        queue = (await c.get(f"{base}/__queue__/{app}")).json()
    return {
        "searched": searched,
        "commands": [c.get("name") for c in cmds],
        "command_count": len(cmds),
        "pages": pages[:4],
        "queue_reads": queue.get("requests"),
    }


async def main() -> None:
    from houndarr.database import init_db

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        import os

        os.environ["HOUNDARR_DATA_DIR"] = str(Path(tmp))
        await init_db()
        # search_log carries an FK to instances, so the row has to exist.
        from houndarr.database import get_db

        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO instances (id, name, type, url) VALUES (1, 'Probe', 'sonarr', 'x')"
            )
            await conn.commit()
        async with _mock_server(port):
            for mode_name, context in (("item mode", False), ("context mode", True)):
                print(f"\n=== {mode_name} ===")
                for app in APPS:
                    if context and APPS[app] is None:
                        print(f"  {app:<12} (item-level only, no context mode)")
                        continue
                    try:
                        r = await _run_app(base, app, context)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {app:<12} FAILED {type(exc).__name__}: {exc}")
                        continue
                    ok = "ok " if r["command_count"] > 0 else "NO DISPATCH"
                    print(
                        f"  {app:<12} {ok} searched={r['searched']} "
                        f"commands={r['command_count']}{set(r['commands']) or ''} "
                        f"queue_reads={r['queue_reads']} pages={r['pages']}"
                    )


if __name__ == "__main__":
    asyncio.run(main())

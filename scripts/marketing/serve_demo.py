"""Serve a seeded Houndarr data directory for the screenshot capture script.

The supervisor's search loop is patched to a no-op so it doesn't try to
reach the fake ``http://sonarr:8989`` etc. URLs. The dashboard renders
the seeded snapshot values without the supervisor generating error rows
in the background.

Defaults to port 8902 and ``./marketing-data`` to match the defaults of
``seed_demo_data.py`` and ``capture_screenshots.py``. Ctrl-C stops the
server cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))


async def _noop(*_args: object, **_kwargs: object) -> None:
    """Replacement for ``run_instance_search``. Sleeps forever so the supervisor
    thinks it has a long-running cycle and never surfaces error rows."""
    await asyncio.sleep(3600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "marketing-data",
        help="Seeded Houndarr data directory.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8902)
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    if not (data_dir / "houndarr.db").exists():
        raise SystemExit(f"[serve] no houndarr.db at {data_dir}; run seed_demo_data.py first.")
    os.environ["HOUNDARR_DATA_DIR"] = str(data_dir)

    # Reset the config singleton so the new HOUNDARR_DATA_DIR is picked up.
    from houndarr import config as config_mod
    from houndarr.engine import search_loop as _sl
    from houndarr.engine import supervisor as _sup

    config_mod._runtime_settings = None  # noqa: SLF001

    # Patch both the definition and the already-imported reference in the
    # supervisor module so the supervisor's calls are intercepted.
    _sl.run_instance_search = _noop  # type: ignore[assignment]
    _sup.run_instance_search = _noop  # type: ignore[assignment]

    # Trap SIGTERM / SIGINT so `kill <pid>` exits with 0 instead of the
    # shell convention 128+signal (143 / 130). This is a throwaway demo
    # server; graceful shutdown isn't meaningful. Returning 0 keeps the
    # background-task harness from flagging every teardown as a failure.
    import signal

    def _handle_signal(signum: int, frame: object | None) -> None:  # noqa: ARG001
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    import uvicorn

    print(f"[serve] http://{args.host}:{args.port}  data-dir={data_dir}")
    uvicorn.run(
        "houndarr.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()

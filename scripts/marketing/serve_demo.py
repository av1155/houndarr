"""Serve a seeded Houndarr data directory for the screenshot capture script.

The supervisor's search loop is patched to a no-op so it doesn't try to
reach the fake ``http://sonarr:8989`` etc. URLs. The dashboard renders
the seeded snapshot values without the supervisor generating error rows
in the background. The patch lands at two attribute sites:

* ``engine.search_loop.run_instance_search`` (the definition).
* ``engine.supervisor.run_instance_search`` (the reference the supervisor
  actually calls; Python binds the name at import time, so patching the
  definition alone leaves the supervisor holding a stale reference).

See ``docs/refactor/track-h-notes.md`` for why the patch is kept local
to this script and not hoisted into ``bootstrap_non_web``.

Defaults to port 8902 and ``./marketing-data`` to match the defaults of
``seed_demo_data.py`` and ``capture_screenshots.py``. Ctrl-C stops the
server cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from houndarr.bootstrap import bootstrap_non_web  # noqa: E402


async def _noop(*_args: object, **_kwargs: object) -> int:
    """Replacement for ``run_instance_search``. Sleeps forever so the supervisor
    thinks it has a long-running cycle and never surfaces error rows.

    Return type mirrors the real ``run_instance_search`` (``-> int``) so
    the monkey-patch stays signature-compatible; the value is unreachable
    because the sleep never returns.
    """
    await asyncio.sleep(3600)
    return 0


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

    # Shared non-web bootstrap: pin AppSettings, wire set_db_path, load the
    # Fernet master key, and run init_db (idempotent against the seeded DB).
    bootstrap_non_web(data_dir=str(data_dir))

    # Patch the supervisor's run_instance_search references at both module
    # attribute sites (see module docstring) before uvicorn imports
    # houndarr.app and wires the real supervisor into the lifespan.
    from houndarr.engine import search_loop as _sl
    from houndarr.engine import supervisor as _sup

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

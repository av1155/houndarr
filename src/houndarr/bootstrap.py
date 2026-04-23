"""Shared non-web bootstrap composition for every Houndarr entry point.

Four steps every entry point needs before doing real work:

1. Pin an :class:`~houndarr.config.AppSettings` (CLI overrides win; otherwise
   the env-derived defaults from :func:`~houndarr.config.get_settings`).
2. Ensure the data directory exists on disk.
3. Load or generate the Fernet master key at
   ``<data_dir>/houndarr.masterkey`` via
   :func:`~houndarr.crypto.ensure_master_key`.
4. Point the SQLite helper at ``<data_dir>/houndarr.db`` and run
   :func:`~houndarr.database.init_db` to advance the schema to the current
   version.

Before this module existed, three separate call sites (the ``python -m
houndarr`` CLI, ``scripts/marketing/seed_demo_data.py``, and
``scripts/marketing/serve_demo.py``) each copy-pasted the sequence. The
FastAPI lifespan in :mod:`houndarr.app` keeps its own equivalent steps
so ``create_app`` callers in tests still boot without hitting a
pre-uvicorn bootstrap; both paths call the same idempotent primitives
so running the four steps twice is safe.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal, TypedDict, Unpack

from houndarr import config as _cfg
from houndarr.config import AppSettings, get_settings
from houndarr.crypto import ensure_master_key
from houndarr.database import init_db, set_db_path


class AppSettingsOverrides(TypedDict, total=False):
    """Optional overrides accepted by :func:`bootstrap_non_web`.

    Keys mirror the non-``data_dir`` fields of
    :class:`~houndarr.config.AppSettings`. Every key is optional: callers
    pass only the fields they need (typically the CLI handler forwards
    every flag; scripts pass nothing and let env vars + defaults win).
    """

    host: str
    port: int
    dev: bool
    log_level: str
    secure_cookies: bool
    cookie_samesite: Literal["lax", "strict"]
    trusted_proxies: str
    auth_mode: str
    auth_proxy_header: str
    update_check_repo: str


def bootstrap_non_web(
    data_dir: str,
    **overrides: Unpack[AppSettingsOverrides],
) -> tuple[AppSettings, Path, bytes]:
    """Compose settings, Fernet master key, and DB init for a non-web boot.

    Args:
        data_dir: Filesystem path to the Houndarr data directory. Pinned
            to the returned :class:`AppSettings` ``data_dir`` field and
            exported as ``HOUNDARR_DATA_DIR`` so uvicorn reload children,
            later ``get_settings()`` fallbacks, and subprocesses all see
            the same value.
        **overrides: Additional :class:`AppSettings` field values (``host``,
            ``port``, ``dev``, ``log_level``, ``secure_cookies``,
            ``cookie_samesite``, ``trusted_proxies``, ``auth_mode``,
            ``auth_proxy_header``, ``update_check_repo``). When any
            override is supplied, :class:`AppSettings` is constructed
            directly and pinned into the runtime singleton so the whole
            process agrees on the overridden values. When no overrides
            are supplied, :func:`get_settings` is used so env vars and
            the dataclass defaults still take effect.

    Returns:
        Three-tuple ``(settings, db_path, master_key)``. ``db_path`` is
        the resolved SQLite path (same object as ``settings.db_path``)
        and ``master_key`` is the 32-byte URL-safe base64 Fernet key.

    Notes:
        Must be called from a sync context. The body invokes
        :func:`asyncio.run` to execute :func:`~houndarr.database.init_db`,
        so calling this from inside an already-running event loop raises
        ``RuntimeError: asyncio.run() cannot be called from a running
        event loop``.

        Every call clears ``_runtime_settings`` before resolving the new
        one and, when ``overrides`` are supplied, pins a fresh
        :class:`AppSettings` into the singleton. Back-to-back calls with
        different overrides therefore leave the singleton pinned to the
        last call; callers holding an :class:`AppSettings` reference
        returned by an earlier call keep their own object but disagree
        with the process-wide singleton.
    """
    # Drop any prior pin so tests and repeated script invocations cannot
    # leak stale settings from a previous run (seed_demo_data + serve_demo
    # ship-rebuild the singleton deliberately; the CLI re-pins below).
    _cfg._runtime_settings = None  # noqa: SLF001
    os.environ["HOUNDARR_DATA_DIR"] = data_dir

    if overrides:
        settings = AppSettings(data_dir=data_dir, **overrides)
        _cfg._runtime_settings = settings  # noqa: SLF001
    else:
        settings = get_settings()

    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)

    master_key = ensure_master_key(settings.data_dir)

    set_db_path(str(settings.db_path))
    asyncio.run(init_db())

    return settings, settings.db_path, master_key

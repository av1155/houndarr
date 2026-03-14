"""Application configuration and runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Runtime settings — set by CLI before the app factory is called
# ---------------------------------------------------------------------------

_runtime_settings: AppSettings | None = None


def _parse_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    raw = os.environ.get(name, "").lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default


def get_settings() -> AppSettings:
    """Return current runtime settings, falling back to defaults."""
    if _runtime_settings is not None:
        return _runtime_settings
    return AppSettings(
        data_dir=os.environ.get("HOUNDARR_DATA_DIR", "/data"),
        host=os.environ.get("HOUNDARR_HOST", "0.0.0.0"),
        port=int(os.environ.get("HOUNDARR_PORT", "8877")),
        dev=_parse_bool_env("HOUNDARR_DEV"),
        log_level=os.environ.get("HOUNDARR_LOG_LEVEL", "info").lower(),
        secure_cookies=_parse_bool_env("HOUNDARR_SECURE_COOKIES"),
        trusted_proxies=os.environ.get("HOUNDARR_TRUSTED_PROXIES", ""),
    )


@dataclass
class AppSettings:
    """Startup configuration resolved from CLI flags and environment variables.

    Attributes:
        data_dir: Directory for persistent data (SQLite DB and master key).
        host: Host address to bind the web server to.
        port: Port to bind the web server to.
        dev: Run in development mode (auto-reload, API docs enabled).
        log_level: Log verbosity level.
        secure_cookies: Set the ``Secure`` flag on session cookies.
            Enable when Houndarr is served over HTTPS via a reverse proxy.
            Corresponds to ``HOUNDARR_SECURE_COOKIES`` env var.
        trusted_proxies: Comma-separated list of trusted reverse-proxy IP
            addresses.  When set, ``X-Forwarded-For`` is honoured for client-IP
            detection (rate limiting).  When empty, only the direct connection
            IP is used.  Corresponds to ``HOUNDARR_TRUSTED_PROXIES`` env var.
    """

    data_dir: str = "/data"
    host: str = "0.0.0.0"
    port: int = 8877
    dev: bool = False
    log_level: str = "info"
    secure_cookies: bool = False
    trusted_proxies: str = ""

    # Derived paths (computed from data_dir)
    db_path: Path = field(init=False)
    master_key_path: Path = field(init=False)

    def __post_init__(self) -> None:
        base = Path(self.data_dir)
        self.db_path = base / "houndarr.db"
        self.master_key_path = base / "houndarr.masterkey"

    def trusted_proxy_set(self) -> frozenset[str]:
        """Return the set of trusted proxy IPs (empty = none trusted)."""
        if not self.trusted_proxies.strip():
            return frozenset()
        return frozenset(ip.strip() for ip in self.trusted_proxies.split(",") if ip.strip())


# ---------------------------------------------------------------------------
# Per-instance defaults (used when creating new instances via the UI)
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE: int = 2
DEFAULT_SLEEP_INTERVAL_MINUTES: int = 30
DEFAULT_HOURLY_CAP: int = 4
DEFAULT_COOLDOWN_DAYS: int = 14
DEFAULT_UNRELEASED_DELAY_HOURS: int = 36
DEFAULT_CUTOFF_BATCH_SIZE: int = 1
DEFAULT_CUTOFF_COOLDOWN_DAYS: int = 21
DEFAULT_CUTOFF_HOURLY_CAP: int = 1
DEFAULT_SONARR_SEARCH_MODE: str = "episode"
DEFAULT_LOG_RETENTION_DAYS: int = 30

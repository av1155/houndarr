"""Application configuration and runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Runtime settings — set by CLI before the app factory is called
# ---------------------------------------------------------------------------

_runtime_settings: AppSettings | None = None


def get_settings() -> AppSettings:
    """Return current runtime settings, falling back to defaults."""
    if _runtime_settings is not None:
        return _runtime_settings
    return AppSettings(
        data_dir=os.environ.get("HOUNDARR_DATA_DIR", "/data"),
        host=os.environ.get("HOUNDARR_HOST", "0.0.0.0"),
        port=int(os.environ.get("HOUNDARR_PORT", "8877")),
        dev=os.environ.get("HOUNDARR_DEV", "").lower() in ("1", "true", "yes"),
        log_level=os.environ.get("HOUNDARR_LOG_LEVEL", "info").lower(),
    )


@dataclass
class AppSettings:
    """Startup configuration resolved from CLI flags and environment variables."""

    data_dir: str = "/data"
    host: str = "0.0.0.0"
    port: int = 8877
    dev: bool = False
    log_level: str = "info"

    # Derived paths (computed from data_dir)
    db_path: Path = field(init=False)
    master_key_path: Path = field(init=False)

    def __post_init__(self) -> None:
        base = Path(self.data_dir)
        self.db_path = base / "houndarr.db"
        self.master_key_path = base / "houndarr.masterkey"


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
DEFAULT_LOG_RETENTION_DAYS: int = 30

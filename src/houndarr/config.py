"""Application configuration and runtime settings."""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from pathlib import Path

logger = logging.getLogger(__name__)


class TrustedProxies:
    """Parsed trusted proxy IPs and CIDR subnets with membership testing."""

    __slots__ = ("_addresses", "_networks")

    def __init__(
        self,
        addresses: frozenset[IPv4Address | IPv6Address],
        networks: tuple[IPv4Network | IPv6Network, ...],
    ) -> None:
        self._addresses = addresses
        self._networks = networks

    def __bool__(self) -> bool:
        return bool(self._addresses) or bool(self._networks)

    def __contains__(self, ip_str: object) -> bool:
        if not isinstance(ip_str, str):
            return False
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if addr in self._addresses:
            return True
        return any(addr in net for net in self._networks)


def _parse_trusted_proxies(raw: str) -> TrustedProxies:
    """Parse comma-separated IPs and CIDR subnets into a ``TrustedProxies``."""
    if not raw.strip():
        return TrustedProxies(frozenset(), ())
    addresses: set[IPv4Address | IPv6Address] = set()
    networks: list[IPv4Network | IPv6Network] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "/" in entry:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning(
                    "HOUNDARR_TRUSTED_PROXIES contains an invalid subnet entry; skipping it"
                )
        else:
            try:
                addresses.add(ipaddress.ip_address(entry))
            except ValueError:
                logger.warning("HOUNDARR_TRUSTED_PROXIES contains an invalid IP entry; skipping it")
    return TrustedProxies(frozenset(addresses), tuple(networks))


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
            addresses or CIDR subnets (e.g. ``10.1.1.0/24``).  When set,
            ``X-Forwarded-For`` is honoured for client-IP detection (rate
            limiting).  When empty, only the direct connection IP is used.
            Corresponds to ``HOUNDARR_TRUSTED_PROXIES`` env var.
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
    _trusted_proxy_cache: TrustedProxies | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        base = Path(self.data_dir)
        self.db_path = base / "houndarr.db"
        self.master_key_path = base / "houndarr.masterkey"

    def trusted_proxy_set(self) -> TrustedProxies:
        """Return parsed trusted proxy IPs and subnets (empty = none trusted)."""
        if self._trusted_proxy_cache is not None:
            return self._trusted_proxy_cache
        self._trusted_proxy_cache = _parse_trusted_proxies(self.trusted_proxies)
        return self._trusted_proxy_cache


# ---------------------------------------------------------------------------
# Per-instance defaults (used when creating new instances via the UI)
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE: int = 2
DEFAULT_SLEEP_INTERVAL_MINUTES: int = 30
DEFAULT_HOURLY_CAP: int = 4
DEFAULT_COOLDOWN_DAYS: int = 14
DEFAULT_POST_RELEASE_GRACE_HOURS: int = 6
DEFAULT_CUTOFF_BATCH_SIZE: int = 1
DEFAULT_CUTOFF_COOLDOWN_DAYS: int = 21
DEFAULT_CUTOFF_HOURLY_CAP: int = 1
DEFAULT_SONARR_SEARCH_MODE: str = "episode"
DEFAULT_LIDARR_SEARCH_MODE: str = "album"
DEFAULT_READARR_SEARCH_MODE: str = "book"
DEFAULT_WHISPARR_SEARCH_MODE: str = "episode"
DEFAULT_QUEUE_LIMIT: int = 0
DEFAULT_LOG_RETENTION_DAYS: int = 30

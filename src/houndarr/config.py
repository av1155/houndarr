"""Application configuration and runtime settings.

Houndarr keeps two config surfaces and this module owns one of them.

Ops config lives on :class:`AppSettings` and is resolved from environment
variables at boot.  The CLI in :mod:`houndarr.__main__` propagates its
flags into ``HOUNDARR_*`` env vars before :func:`bootstrap_settings` pins
an :class:`AppSettings`, so uvicorn reload children that re-import the
module pick up the same values via :func:`get_settings`.  Once pinned,
every field is fixed for the life of the process: the operator changes
it by editing ``docker-compose.yml`` (or the systemd unit env) and
restarting the container.  ``HOUNDARR_AUTH_MODE`` is the canonical
example.  Switching from ``builtin`` to ``proxy`` rewires the auth
middleware, which only happens at app construction time;
``HOUNDARR_DATA_DIR``, ``HOUNDARR_TRUSTED_PROXIES``, and
``HOUNDARR_SECURE_COOKIES`` behave the same way for the same reason.

User config lives in SQLite and is editable at runtime through the web
UI without any restart.  The key-value ``settings`` table holds
singletons read and written through
:mod:`houndarr.repositories.settings`; canonical examples are the
authenticated ``username`` and bcrypt ``password_hash`` (changed from
the admin account UI), the boolean ``update_check_enabled`` flag
(Settings > Maintenance), and the ``schema_version`` migration cursor.
The ``instances`` table holds per-instance policy (``batch_size``,
``sleep_interval_mins``, ``hourly_cap``, ``cooldown_days``, the per-app
search-mode columns) read through
:mod:`houndarr.repositories.instances`; the engine picks up new values
on the next supervisor cycle, so the operator can retune the search rate
from ``/settings/instances/<id>`` and see it take effect within minutes.

The two surfaces never overlap.  Anything that needs a process boot to
take effect (network bind, cookie attributes, auth wiring, log level)
lives here as an :class:`AppSettings` field.  Anything an operator
should be able to retune without redeploying lives in the database.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from pathlib import Path
from typing import Literal, TypedDict, Unpack

logger = logging.getLogger(__name__)

# Header names that must never be used as a proxy auth header because they
# carry protocol-level or framework-level semantics that would conflict.
_RESERVED_HEADERS: frozenset[str] = frozenset(
    {
        "host",
        "content-type",
        "content-length",
        "cookie",
        "authorization",
        "x-csrf-token",
        "hx-request",
        "hx-target",
        "hx-trigger",
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
        "connection",
        "upgrade",
    }
)


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
# Runtime settings: set by CLI before the app factory is called
# ---------------------------------------------------------------------------

_runtime_settings: AppSettings | None = None


SameSitePolicy = Literal["lax", "strict"]

_VALID_SAMESITE: frozenset[str] = frozenset({"lax", "strict"})


def _parse_samesite(raw: str) -> SameSitePolicy:
    """Normalise a SameSite env/CLI value, defaulting to ``lax``."""
    value = raw.strip().lower()
    if value in _VALID_SAMESITE:
        return value  # type: ignore[return-value]
    return "lax"


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
        cookie_samesite=_parse_samesite(os.environ.get("HOUNDARR_COOKIE_SAMESITE", "lax")),
        trusted_proxies=os.environ.get("HOUNDARR_TRUSTED_PROXIES", ""),
        auth_mode=os.environ.get("HOUNDARR_AUTH_MODE", "builtin").lower(),
        auth_proxy_header=os.environ.get("HOUNDARR_AUTH_PROXY_HEADER", ""),
    )


class BootstrapOverrides(TypedDict, total=False):
    """Optional :class:`AppSettings` field overrides accepted by ``bootstrap_settings``.

    Every key is optional. Supplied keys are forwarded directly to the
    :class:`AppSettings` constructor; unsupplied keys take whatever default
    the dataclass declares (the env-var fallback in :func:`get_settings`
    is only consulted on the no-override branch, never on the explicit
    override path).
    """

    data_dir: str
    host: str
    port: int
    dev: bool
    log_level: str
    secure_cookies: bool
    cookie_samesite: SameSitePolicy
    trusted_proxies: str
    auth_mode: str
    auth_proxy_header: str


def bootstrap_settings(**overrides: Unpack[BootstrapOverrides]) -> AppSettings:
    """Resolve, pin, and return the runtime ``AppSettings`` honouring overrides.

    This is the single entry point for installing :class:`AppSettings` into
    the module-level singleton. CLI boot, scripts, and tests all funnel
    through it instead of reaching into ``_runtime_settings`` directly,
    so the pin lifecycle is observable in one place.

    With at least one override supplied, an :class:`AppSettings` is
    constructed from the kwargs and pinned. Subsequent
    :func:`get_settings` calls return the same instance until the next
    ``bootstrap_settings`` call (or process restart). Any prior pin is
    replaced; any env var for an *unsupplied* key is ignored (the kwarg
    path matches the pre-refactor ``AppSettings(data_dir=..., **overrides)``
    shape, where missing fields take the dataclass default rather than the
    env value).

    With no overrides supplied, any prior pin is cleared and the result of
    :func:`get_settings` (env-var resolved) is returned *without* pinning.
    Callers that subsequently change env vars therefore still see those
    changes through :func:`get_settings`.

    Args:
        **overrides: Optional :class:`AppSettings` field values.

    Returns:
        The :class:`AppSettings` now visible to subsequent
        :func:`get_settings` calls. With overrides this is the freshly
        pinned instance; without overrides this is an unpinned
        env-resolved instance.
    """
    global _runtime_settings  # noqa: PLW0603

    if not overrides:
        _runtime_settings = None
        return get_settings()

    settings = AppSettings(**overrides)
    _runtime_settings = settings
    return settings


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
        cookie_samesite: ``SameSite`` attribute for session and CSRF cookies.
            ``lax`` (default) allows cookies on top-level navigations from
            external links (e.g. dashboard apps) while blocking cross-site
            form submissions.  ``strict`` withholds cookies on all
            cross-site requests, which prevents access via external links
            but provides an extra layer of isolation.
            Corresponds to ``HOUNDARR_COOKIE_SAMESITE`` env var.
        trusted_proxies: Comma-separated list of trusted reverse-proxy IP
            addresses or CIDR subnets (e.g. ``10.1.1.0/24``).  When set,
            ``X-Forwarded-For`` is honoured for client-IP detection (rate
            limiting).  When empty, only the direct connection IP is used.
            Corresponds to ``HOUNDARR_TRUSTED_PROXIES`` env var.
        auth_mode: Authentication mode. ``builtin`` (default) uses local
            session-based auth; ``proxy`` delegates authentication to a
            reverse proxy via a trusted header.
            Corresponds to ``HOUNDARR_AUTH_MODE`` env var.
        auth_proxy_header: HTTP header name carrying the authenticated
            username from the reverse proxy (e.g. ``Remote-User``).
            Required when ``auth_mode`` is ``proxy``.
            Corresponds to ``HOUNDARR_AUTH_PROXY_HEADER`` env var.
    """

    data_dir: str = "/data"
    host: str = "0.0.0.0"
    port: int = 8877
    dev: bool = False
    log_level: str = "info"
    secure_cookies: bool = False
    cookie_samesite: Literal["lax", "strict"] = "lax"
    trusted_proxies: str = ""
    auth_mode: str = "builtin"
    auth_proxy_header: str = ""

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

    def validate_auth_config(self) -> list[str]:
        """Return fatal configuration errors for authentication settings.

        Returns:
            List of error messages.  Empty means the configuration is valid.
        """
        errors: list[str] = []
        if self.cookie_samesite not in ("lax", "strict"):
            errors.append(
                f"HOUNDARR_COOKIE_SAMESITE must be 'lax' or 'strict', got '{self.cookie_samesite}'"
            )
        if self.auth_mode not in ("builtin", "proxy"):
            errors.append(
                f"HOUNDARR_AUTH_MODE must be 'builtin' or 'proxy', got '{self.auth_mode}'"
            )
            return errors
        if self.auth_mode == "proxy":
            if not self.auth_proxy_header.strip():
                errors.append(
                    "HOUNDARR_AUTH_PROXY_HEADER is required when HOUNDARR_AUTH_MODE=proxy"
                )
            else:
                header_lower = self.auth_proxy_header.strip().lower()
                if header_lower in _RESERVED_HEADERS:
                    errors.append(
                        f"HOUNDARR_AUTH_PROXY_HEADER '{self.auth_proxy_header}' "
                        f"conflicts with a reserved HTTP header"
                    )
            if not self.trusted_proxies.strip():
                errors.append(
                    "HOUNDARR_TRUSTED_PROXIES is required when HOUNDARR_AUTH_MODE=proxy "
                    "(without trusted proxies, any client can forge the auth header)"
                )
        return errors


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
DEFAULT_WHISPARR_V2_SEARCH_MODE: str = "episode"
DEFAULT_QUEUE_LIMIT: int = 0
DEFAULT_ALLOWED_TIME_WINDOW: str = ""
DEFAULT_SEARCH_ORDER: str = "random"
DEFAULT_LOG_RETENTION_DAYS: int = 30

# Upgrade search defaults (third, opt-in pass; very conservative)
DEFAULT_UPGRADE_BATCH_SIZE: int = 1
DEFAULT_UPGRADE_COOLDOWN_DAYS: int = 90
DEFAULT_UPGRADE_HOURLY_CAP: int = 1
DEFAULT_UPGRADE_SONARR_SEARCH_MODE: str = "episode"
DEFAULT_UPGRADE_LIDARR_SEARCH_MODE: str = "album"
DEFAULT_UPGRADE_READARR_SEARCH_MODE: str = "book"
DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE: str = "episode"

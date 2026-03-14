"""Instance URL validation — guards against SSRF and obviously unsafe targets.

Self-hosted context notes
--------------------------
Houndarr is designed to talk to Sonarr and Radarr instances that commonly run
on the same Docker network or LAN as Houndarr itself.  Private IP ranges are
therefore *legitimate and expected* targets.  This module does **not** block
RFC-1918 ranges wholesale; instead it only rejects the most obviously dangerous
targets (loopback and link-local metadata service addresses) while accepting
private network ranges needed for Docker / LAN setups.

Operators who need to allow even loopback targets (e.g. ``localhost``) for
unusual setups can do so by reading the validation error and configuring
their instance URL appropriately (use the container name / hostname instead
of ``127.0.0.1``).
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = frozenset(["http", "https"])

# Loopback ranges blocked by default.  Private ranges (10/8, 172.16/12,
# 192.168/16) are NOT blocked because Docker / LAN setups legitimately use them.
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.IPv4Network("127.0.0.0/8"),  # IPv4 loopback
    ipaddress.IPv6Network("::1/128"),  # IPv6 loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local / cloud metadata (e.g. 169.254.169.254)
    ipaddress.IPv6Network("fe80::/10"),  # IPv6 link-local
]

# Hostnames that should never be used directly — operators must use container
# names or FQDNs instead.
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(["localhost"])

# Valid hostname pattern (RFC 1123 relaxed)
_HOSTNAME_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_instance_url(url: str) -> str | None:
    """Return an error message if *url* is unsafe or malformed, else ``None``.

    Checks performed:
    - Scheme must be ``http`` or ``https``
    - Host must be present and non-empty
    - Host must not be a blocked hostname (``localhost``)
    - If the host is a numeric IP address, it must not be in a blocked range
      (loopback, link-local)

    Private IP ranges (RFC-1918) are intentionally allowed so that Docker /
    LAN deployments work without restriction.

    Args:
        url: The URL to validate (e.g. ``http://sonarr:8989``).

    Returns:
        A human-readable error string, or ``None`` if the URL is acceptable.
    """
    if not url or not url.strip():
        return "Instance URL is required."

    try:
        parsed = urlparse(url.strip())
    except Exception:
        return "Instance URL could not be parsed. Use http:// or https://."

    # Scheme check
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return f"Instance URL scheme '{scheme}' is not allowed. Use http:// or https://."

    # Host check
    host = parsed.hostname or ""
    if not host:
        return "Instance URL must include a host (e.g. http://sonarr:8989)."

    # Blocked hostname check
    if host.lower() in _BLOCKED_HOSTNAMES:
        return (
            f"Instance URL host '{host}' is not allowed. "
            "Use the container name or network hostname instead of 'localhost'."
        )

    # IP address range check
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP address — hostname, which is fine unless blocked above
        return None

    for blocked_net in _BLOCKED_NETWORKS:
        if addr in blocked_net:
            return (
                f"Instance URL points to a blocked address range ({addr}). "
                "Use a container name or routable hostname."
            )

    return None

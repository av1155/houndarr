"""Instance URL validation — guards against SSRF and obviously unsafe targets.

Self-hosted context notes
--------------------------
Houndarr is designed to talk to Sonarr and Radarr instances that commonly run
on the same Docker network or LAN as Houndarr itself.  Private IP ranges are
therefore *legitimate and expected* targets.  This module does **not** block
RFC-1918 ranges wholesale; instead it only rejects the most obviously dangerous
targets (loopback and link-local metadata service addresses) while accepting
private network ranges needed for Docker / LAN setups.

Both literal IPs and resolved hostname addresses are checked against blocked
targets, so aliases that resolve to loopback/link-local ranges are rejected.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = frozenset(["http", "https"])

# Loopback ranges blocked by default.  Private ranges (10/8, 172.16/12,
# 192.168/16) are NOT blocked because Docker / LAN setups legitimately use them.
# Hostnames that should never be used directly — operators must use container
# names or FQDNs instead.
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(["localhost"])

# Valid hostname pattern (RFC 1123 relaxed)
_HOSTNAME_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
)


def _is_blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether *addr* falls into a blocked network range."""
    return addr.is_loopback or addr.is_link_local or addr.is_unspecified


def _resolve_hostname_ips(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve *host* to concrete IP addresses for SSRF safety checks.

    Returns:
        Set of resolved IP addresses. If the hostname cannot be resolved at
        validation time, an empty set is returned and connectivity checks can
        surface the operator-facing error.

    Raises:
        ValueError: If hostname is malformed.
    """
    if _HOSTNAME_PATTERN.fullmatch(host) is None:
        raise ValueError(f"Instance URL host '{host}' is invalid.")

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return set()

    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family == socket.AF_INET:
            resolved.add(ipaddress.IPv4Address(sockaddr[0]))
        elif family == socket.AF_INET6:
            resolved.add(ipaddress.IPv6Address(sockaddr[0]))

    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_instance_url(url: str) -> str | None:
    """Return an error message if *url* is unsafe or malformed, else ``None``.

    Checks performed:
    - Scheme must be ``http`` or ``https``
    - Host must be present and non-empty
    - Host must not be a blocked hostname (``localhost``)
    - If the host is a numeric IP address, it must not be in a blocked class
      (loopback, link-local, unspecified)

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

    # IP address range check (literal IP host)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Hostname: resolve and apply the same blocked-network rules to avoid
        # aliases/bypass (e.g. a hostname resolving to 127.0.0.1).
        try:
            resolved_ips = _resolve_hostname_ips(host)
        except ValueError:
            return f"Instance URL host '{host}' is invalid."

        for resolved in resolved_ips:
            if _is_blocked_address(resolved):
                return (
                    f"Instance URL host '{host}' resolves to a blocked address range ({resolved}). "
                    "Use a container name or routable hostname."
                )

        # If hostname does not currently resolve, defer the operator-facing
        # failure to the explicit connection test.
        return None

    if _is_blocked_address(addr):
        return (
            f"Instance URL points to a blocked address range ({addr}). "
            "Use a container name or routable hostname."
        )

    return None

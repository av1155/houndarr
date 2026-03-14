"""Tests for instance URL validation (SSRF hardening)."""

from __future__ import annotations

import socket

import pytest

from houndarr.services.url_validation import validate_instance_url

# ---------------------------------------------------------------------------
# Valid URLs — should return None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://sonarr:8989",
        "http://radarr:7878",
        "https://sonarr.example.com",
        "http://192.168.1.100:8989",  # private range — allowed for LAN/Docker
        "http://10.0.0.5:8989",  # private range — allowed
        "http://172.16.0.10:8989",  # private range — allowed
        "http://my-sonarr:8989",
        "https://sonarr.home.arpa:8989",
        "http://sonarr",  # bare hostname without port
    ],
)
def test_valid_urls_pass(url: str) -> None:
    """Valid self-hosted instance URLs should not trigger an error."""
    assert validate_instance_url(url) is None


# ---------------------------------------------------------------------------
# Invalid scheme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://sonarr:8989",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ws://sonarr:8989",
        "//sonarr:8989",
    ],
)
def test_invalid_scheme_rejected(url: str) -> None:
    """Non-http/https scheme must be rejected."""
    result = validate_instance_url(url)
    assert result is not None
    assert "scheme" in result.lower() or "not allowed" in result.lower()


# ---------------------------------------------------------------------------
# Blocked loopback / link-local IP ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8989",
        "http://127.1.2.3:8989",
        "http://169.254.169.254",  # AWS/GCP/Azure metadata endpoint
        "http://169.254.0.1",  # link-local range
    ],
)
def test_loopback_and_link_local_rejected(url: str) -> None:
    """Loopback and link-local IP addresses must be blocked."""
    result = validate_instance_url(url)
    assert result is not None
    assert "blocked" in result.lower() or "not allowed" in result.lower()


# ---------------------------------------------------------------------------
# Blocked hostname
# ---------------------------------------------------------------------------


def test_localhost_hostname_rejected() -> None:
    """'localhost' hostname must be rejected (use container name instead)."""
    result = validate_instance_url("http://localhost:8989")
    assert result is not None
    assert "localhost" in result.lower() or "not allowed" in result.lower()


def test_localhost_https_rejected() -> None:
    result = validate_instance_url("https://localhost:8989")
    assert result is not None


# ---------------------------------------------------------------------------
# Missing / empty inputs
# ---------------------------------------------------------------------------


def test_empty_url_rejected() -> None:
    result = validate_instance_url("")
    assert result is not None


def test_whitespace_url_rejected() -> None:
    result = validate_instance_url("   ")
    assert result is not None


def test_missing_host_rejected() -> None:
    result = validate_instance_url("http://")
    assert result is not None
    assert "host" in result.lower()


# ---------------------------------------------------------------------------
# Hostname resolution safety checks
# ---------------------------------------------------------------------------


def test_hostname_resolving_to_loopback_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hostnames that resolve to loopback must be rejected."""

    def _fake_getaddrinfo(host: str, port: object, type: int) -> list[tuple[object, ...]]:
        assert host == "alias.local"
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8989))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    result = validate_instance_url("http://alias.local:8989")
    assert result is not None
    assert "blocked" in result.lower()


def test_hostname_resolving_to_link_local_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hostnames that resolve to link-local addresses must be rejected."""

    def _fake_getaddrinfo(host: str, port: object, type: int) -> list[tuple[object, ...]]:
        assert host == "metadata.internal"
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    result = validate_instance_url("http://metadata.internal")
    assert result is not None
    assert "blocked" in result.lower()


def test_hostname_resolving_to_private_lan_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hostnames resolving to RFC1918 addresses remain valid for self-hosting."""

    def _fake_getaddrinfo(host: str, port: object, type: int) -> list[tuple[object, ...]]:
        assert host == "sonarr.internal"
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.25", 8989))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    assert validate_instance_url("http://sonarr.internal:8989") is None


def test_unresolvable_hostname_defers_to_connection_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unresolvable hostnames are allowed here; connection test reports failure."""

    def _fake_getaddrinfo(host: str, port: object, type: int) -> list[tuple[object, ...]]:
        raise socket.gaierror("name not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    assert validate_instance_url("http://unknown.internal:8989") is None

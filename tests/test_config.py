"""Tests for AppSettings configuration validation."""

from __future__ import annotations

import pytest

from houndarr.config import AppSettings

# ---------------------------------------------------------------------------
# validate_auth_config — builtin mode (default, always valid)
# ---------------------------------------------------------------------------


def test_validate_builtin_mode_default() -> None:
    """Default settings (builtin mode) produce no errors."""
    settings = AppSettings(data_dir="/tmp/test")
    assert settings.validate_auth_config() == []


def test_validate_builtin_mode_explicit() -> None:
    """Explicitly setting auth_mode='builtin' is valid without proxy settings."""
    settings = AppSettings(data_dir="/tmp/test", auth_mode="builtin")
    assert settings.validate_auth_config() == []


# ---------------------------------------------------------------------------
# validate_auth_config — proxy mode (valid configurations)
# ---------------------------------------------------------------------------


def test_validate_proxy_mode_valid() -> None:
    """Proxy mode with header and trusted proxies is valid."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="Remote-User",
        trusted_proxies="10.0.0.1",
    )
    assert settings.validate_auth_config() == []


def test_validate_proxy_mode_valid_cidr() -> None:
    """Proxy mode accepts CIDR subnets in trusted proxies."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="X-authentik-username",
        trusted_proxies="172.18.0.0/16",
    )
    assert settings.validate_auth_config() == []


# ---------------------------------------------------------------------------
# validate_auth_config — proxy mode (invalid configurations)
# ---------------------------------------------------------------------------


def test_validate_proxy_mode_missing_header() -> None:
    """Proxy mode without auth header is rejected."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="",
        trusted_proxies="10.0.0.1",
    )
    errors = settings.validate_auth_config()
    assert len(errors) >= 1
    assert "HOUNDARR_AUTH_PROXY_HEADER" in errors[0]


def test_validate_proxy_mode_missing_trusted_proxies() -> None:
    """Proxy mode without trusted proxies is rejected."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="Remote-User",
        trusted_proxies="",
    )
    errors = settings.validate_auth_config()
    assert any("HOUNDARR_TRUSTED_PROXIES" in e for e in errors)


def test_validate_proxy_mode_missing_both() -> None:
    """Proxy mode without both header and trusted proxies returns two errors."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="",
        trusted_proxies="",
    )
    errors = settings.validate_auth_config()
    assert len(errors) == 2


def test_validate_proxy_mode_whitespace_header() -> None:
    """Whitespace-only header is rejected."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="   ",
        trusted_proxies="10.0.0.1",
    )
    errors = settings.validate_auth_config()
    assert any("HOUNDARR_AUTH_PROXY_HEADER" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_auth_config — reserved header blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Host",
        "Cookie",
        "Authorization",
        "X-CSRF-Token",
        "HX-Request",
        "X-Forwarded-For",
        "Content-Type",
        "Connection",
    ],
)
def test_validate_proxy_mode_reserved_header(header: str) -> None:
    """Reserved HTTP headers are rejected as proxy auth headers."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header=header,
        trusted_proxies="10.0.0.1",
    )
    errors = settings.validate_auth_config()
    assert len(errors) == 1
    assert "reserved" in errors[0].lower()


def test_validate_proxy_mode_nonreserved_header() -> None:
    """Non-reserved headers like Remote-User are accepted."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="proxy",
        auth_proxy_header="Remote-User",
        trusted_proxies="10.0.0.1",
    )
    assert settings.validate_auth_config() == []


# ---------------------------------------------------------------------------
# validate_auth_config — invalid auth mode
# ---------------------------------------------------------------------------


def test_validate_invalid_auth_mode() -> None:
    """An unrecognized auth mode is rejected."""
    settings = AppSettings(
        data_dir="/tmp/test",
        auth_mode="oauth",
    )
    errors = settings.validate_auth_config()
    assert len(errors) == 1
    assert "builtin" in errors[0]
    assert "proxy" in errors[0]

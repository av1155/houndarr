"""Tests for AppSettings configuration validation."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from houndarr.config import AppSettings, bootstrap_settings, get_settings

# ---------------------------------------------------------------------------
# validate_auth_config - builtin mode (default, always valid)
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
# validate_auth_config - proxy mode (valid configurations)
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
# validate_auth_config - proxy mode (invalid configurations)
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
# validate_auth_config - reserved header blocklist
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
# validate_auth_config - invalid auth mode
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


# ---------------------------------------------------------------------------
# bootstrap_settings: override precedence + singleton lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture()
def _isolate_pin() -> Generator[None, None, None]:
    """Clear the runtime-settings pin before and after each test in this section.

    bootstrap_settings pins a module-level singleton; without isolation
    these tests would leak state into each other (and into unrelated
    tests sharing the worker process under pytest-xdist).
    """
    bootstrap_settings()
    yield
    bootstrap_settings()


def test_bootstrap_settings_with_overrides_pins_into_get_settings(
    _isolate_pin: None,
) -> None:
    """An override survives via get_settings until the next bootstrap_settings call."""
    bootstrap_settings(data_dir="/tmp/test", port=9000)
    assert get_settings().port == 9000


def test_bootstrap_settings_override_wins_over_env_var(
    _isolate_pin: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit overrides take precedence over HOUNDARR_* env vars."""
    monkeypatch.setenv("HOUNDARR_PORT", "8000")
    bootstrap_settings(data_dir="/tmp/test", port=9000)
    assert get_settings().port == 9000


def test_bootstrap_settings_unsupplied_keys_take_dataclass_defaults(
    _isolate_pin: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupplied override keys take the dataclass default, not the env var.

    The override path builds :class:`AppSettings` from overrides
    alone; env vars are only consulted on the no-override fallback
    through :func:`get_settings`.
    """
    monkeypatch.setenv("HOUNDARR_PORT", "7777")
    bootstrap_settings(data_dir="/tmp/test")
    assert get_settings().port == 8877


def test_bootstrap_settings_no_overrides_clears_prior_pin(_isolate_pin: None) -> None:
    """Calling bootstrap_settings() with no kwargs drops the pinned override."""
    bootstrap_settings(data_dir="/tmp/test", port=9000)
    bootstrap_settings()
    # Singleton is unpinned; get_settings re-resolves from env / defaults.
    assert get_settings().port == 8877


def test_bootstrap_settings_no_overrides_returns_env_resolved(
    _isolate_pin: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-overrides return value reflects current env vars."""
    monkeypatch.setenv("HOUNDARR_PORT", "7777")
    settings = bootstrap_settings()
    assert settings.port == 7777


def test_bootstrap_settings_back_to_back_overrides_replace(_isolate_pin: None) -> None:
    """Each call replaces the prior pin; earlier overrides do not bleed through."""
    bootstrap_settings(data_dir="/tmp/a", port=9000)
    bootstrap_settings(data_dir="/tmp/b", host="127.0.0.1")
    pinned = get_settings()
    assert pinned.data_dir == "/tmp/b"
    assert pinned.host == "127.0.0.1"
    # port from the first call must not survive into the second pin.
    assert pinned.port == 8877


def test_bootstrap_settings_returns_pinned_instance(_isolate_pin: None) -> None:
    """The returned AppSettings is the same object get_settings hands back."""
    pinned = bootstrap_settings(data_dir="/tmp/test", port=9000)
    assert get_settings() is pinned

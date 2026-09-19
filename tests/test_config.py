"""Tests for AppSettings configuration validation."""

from __future__ import annotations

import importlib.util
import os
import signal
import zoneinfo
from collections.abc import Generator
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from houndarr import config
from houndarr.config import AppSettings, bootstrap_settings, get_settings, unresolved_timezone
from tests.conftest import libc_timezone

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
def _isolate_pin() -> Generator[None]:
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


# ---------------------------------------------------------------------------
# unresolved_timezone
# ---------------------------------------------------------------------------

# Captured before any test narrows TZPATH, so the absolute-path case can find a
# genuine TZif file on whichever host the suite runs on.
_REAL_ZONE_FILE = next(
    (p for base in zoneinfo.TZPATH if (p := Path(base) / "UTC").is_file()),
    None,
)


@pytest.fixture(autouse=True)
def _ignore_ambient_tzdir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide any TZDIR the developer's own machine exports.

    NixOS and some base images set one. Every case below is about how the two
    resolvers compare, which the TZDIR bail-out short-circuits, so an inherited
    value would silently turn those assertions into no-ops. The two tests that
    are about TZDIR set it themselves afterwards, which still wins.
    """
    monkeypatch.delenv("TZDIR", raising=False)


@pytest.fixture()
def _no_zone_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Point zoneinfo at an empty directory so no IANA key resolves.

    Without this the host decides the outcome: US/Eastern loads on macOS and
    on Debian with tzdata-legacy installed, so the assertions below would pass
    for the wrong reason on some machines and fail on others.

    TZDIR goes with it.  Narrowing TZPATH would otherwise make any exported
    TZDIR look like a redirect, and a NixOS dev box would see these cases bail
    out early instead of running.
    """
    monkeypatch.delenv("TZDIR", raising=False)
    # zoneinfo falls back to the PyPI tzdata package, which carries the very
    # aliases these tests expect to be missing.  Adding it to the lock file
    # would quietly turn every case below into a no-op, so say so loudly.
    assert importlib.util.find_spec("tzdata") is None, (
        "the PyPI tzdata package shadows the system zone database; "
        "these tests would silently stop asserting anything"
    )
    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    ZoneInfo.clear_cache()
    yield
    zoneinfo.reset_tzpath()
    ZoneInfo.clear_cache()


@pytest.mark.parametrize(
    "tz",
    [
        "US/Eastern",
        "Japan",
        "GB",
        "Asia/Calcutta",
        "America/New_Yrok",
        "america/new_york",
        "America/New_York ",
        "   ",
        "America",
        "Etc",
        "localtime",
        "EST",
        "MST",
        "../../etc/passwd",
    ],
)
def test_unresolved_timezone_reports_a_zone_with_no_file(_no_zone_files: None, tz: str) -> None:
    """A name with no zone file and no POSIX offset leaves local time on UTC."""
    assert unresolved_timezone(tz) == tz


@pytest.mark.parametrize(
    "tz",
    [
        "EST5EDT",
        "CST6CDT",
        "PST8PDT",
        "UTC0",
        "GMT0",
        "GMT0BST,M3.5.0/1,M10.5.0",
        "<+0530>5:30",
    ],
)
def test_unresolved_timezone_accepts_a_posix_spec(_no_zone_files: None, tz: str) -> None:
    """The C library parses these itself, so a missing zone file is irrelevant.

    Matching on the spec's shape rather than on the resulting offset matters:
    GMT0BST sits at UTC+0 every winter, so an offset test would report a
    working configuration for half the year.
    """
    assert unresolved_timezone(tz) is None


@pytest.mark.parametrize("tz", [None, ""])
def test_unresolved_timezone_ignores_an_unset_value(tz: str | None) -> None:
    """No TZ at all means UTC by choice, which is not a misconfiguration."""
    assert unresolved_timezone(tz) is None


@pytest.mark.parametrize("tz", ["UTC", "Etc/UTC", ":UTC"])
def test_unresolved_timezone_accepts_a_zone_both_resolvers_agree_on(tz: str) -> None:
    """A real zone the C library also loads is reported as trustworthy."""
    with libc_timezone(tz):
        assert unresolved_timezone(tz) is None


@pytest.mark.skipif(_REAL_ZONE_FILE is None, reason="host has no zoneinfo database")
def test_unresolved_timezone_accepts_an_absolute_path_to_a_zone_file() -> None:
    """TZ may name a zone file directly, and the C library opens it."""
    assert _REAL_ZONE_FILE is not None
    with libc_timezone(str(_REAL_ZONE_FILE)):
        assert unresolved_timezone(str(_REAL_ZONE_FILE)) is None


def test_unresolved_timezone_reports_an_absolute_path_that_is_not_a_zone(
    tmp_path: Path,
) -> None:
    """A readable file that is not zone data still leaves the C library on UTC.

    An existence check passes here, which is why the file is parsed instead.
    """
    decoy = tmp_path / "passwd"
    decoy.write_text("root:x:0:0:root:/root:/bin/sh\n")
    assert unresolved_timezone(str(decoy)) == str(decoy)


def test_unresolved_timezone_reports_an_absolute_path_that_is_missing(tmp_path: Path) -> None:
    """A mistyped zone-file path is reported rather than silently ignored."""
    missing = str(tmp_path / "no-such-zone")
    assert unresolved_timezone(missing) == missing


def test_unresolved_timezone_reports_an_absolute_path_that_is_a_directory(
    tmp_path: Path,
) -> None:
    """A directory is not zone data, and opening it raises rather than parses."""
    assert unresolved_timezone(str(tmp_path)) == str(tmp_path)


def test_unresolved_timezone_reports_a_split_resolver_for_a_winter_utc_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zone that sits at UTC+0 half the year must not hide a split resolver.

    Europe/London is indistinguishable from a silent UTC fallback every
    January, so comparing a single instant would let a container booted in
    winter run its whole summer an hour off.
    """
    london = ZoneInfo("Europe/London")
    monkeypatch.setattr(config, "ZoneInfo", lambda _key: london)
    with libc_timezone("UTC"):
        assert unresolved_timezone("GB") == "GB"


def test_unresolved_timezone_reports_a_split_resolver_for_a_summer_utc_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror of the case above, because that one only bites half the year.

    With the C library on Europe/London and zoneinfo pinned to a fixed +01:00
    the two agree all summer and diverge all winter, which is the opposite
    season to the case above.  Collapsing the comparison back to a single
    instant therefore fails one of the pair whatever the date.
    """
    monkeypatch.setattr(config, "ZoneInfo", lambda _key: timezone(timedelta(hours=1)))
    with libc_timezone("Europe/London"):
        assert unresolved_timezone("GB") == "GB"


@pytest.mark.skipif(_REAL_ZONE_FILE is None, reason="host has no zoneinfo database")
def test_unresolved_timezone_still_checks_when_tzdir_names_a_searched_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TZDIR naming a directory zoneinfo already reads is not a redirect.

    NixOS and some base images export one as a matter of course, and dropping
    the check for them would be a blind spot bought for nothing.
    """
    assert _REAL_ZONE_FILE is not None
    monkeypatch.setenv("TZDIR", str(_REAL_ZONE_FILE.parent))
    with libc_timezone(None):
        assert unresolved_timezone("Not/AZone") == "Not/AZone"


@pytest.mark.skipif(_REAL_ZONE_FILE is None, reason="host has no zoneinfo database")
def test_unresolved_timezone_still_checks_an_absolute_path_under_a_redirected_tzdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TZDIR cannot excuse an absolute path, because the C library ignores it.

    Given TZ=/some/file the zone is opened directly, so a redirected TZDIR
    changes nothing about whether that file is readable zone data.
    """
    monkeypatch.setenv("TZDIR", str(tmp_path / "nowhere"))
    decoy = tmp_path / "passwd"
    decoy.write_text("root:x:0:0:root:/root:/bin/sh\n")
    assert unresolved_timezone(str(decoy)) == str(decoy)


def test_unresolved_timezone_reports_a_truncated_zone_file(tmp_path: Path) -> None:
    """Damaged zone data is reported, not raised.

    Parsing a truncated TZif raises struct.error, which subclasses neither
    OSError nor ValueError.  This runs before the app has started, so an
    escape would turn a clock reading UTC into a process that never boots.
    """
    truncated = tmp_path / "truncated"
    truncated.write_bytes(b"TZif" + b"2" + b"\x00" * 15 + b"\x00\x00\x00\x01")
    assert unresolved_timezone(str(truncated)) == str(truncated)


class _AlarmFired(BaseException):
    """Raised by the FIFO alarm below, deliberately outside ``Exception``.

    ``TimeoutError`` would be the obvious choice and is exactly wrong: it
    subclasses ``OSError``, which ``unresolved_timezone`` catches, so the
    alarm would be swallowed by the very handler the guard exists to keep
    the code away from, and the test would pass ten seconds late instead of
    failing.
    """


def test_unresolved_timezone_reports_a_fifo_without_blocking(tmp_path: Path) -> None:
    """A FIFO is not a zone file, and opening one would hang startup forever.

    The alarm is what lets this test fail rather than hang.  Without the
    regular-file guard the open never returns, and an unbounded hang wedges an
    xdist worker until the whole CI job times out.
    """
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    def _give_up(*_: object) -> None:
        raise _AlarmFired

    previous = signal.signal(signal.SIGALRM, _give_up)
    signal.alarm(5)
    try:
        assert unresolved_timezone(str(fifo)) == str(fifo)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_unresolved_timezone_stays_quiet_when_tzdir_redirects_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TZDIR moves the C library's search path but not zoneinfo's.

    The two then describe different databases, so a mismatch says nothing
    about the operator's clock and a warning would be pure noise.
    """
    monkeypatch.setenv("TZDIR", "/somewhere/else")
    assert unresolved_timezone("Not/AZone") is None


def test_unresolved_timezone_reports_a_zone_the_c_library_disagrees_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """zoneinfo finding a zone the C library cannot is still a broken clock.

    Installing the PyPI tzdata package without the system legacy zones would
    produce exactly this split, so the offsets are compared rather than
    trusting either resolver alone.
    """
    monkeypatch.setattr(config, "ZoneInfo", lambda _key: timezone(timedelta(hours=5, minutes=30)))
    with libc_timezone("UTC"):
        assert unresolved_timezone("Asia/Calcutta") == "Asia/Calcutta"

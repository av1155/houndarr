"""Tests for application startup behavior in lifespan."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from houndarr.app import create_app
from houndarr.engine.supervisor import Supervisor
from tests.conftest import libc_timezone


def test_startup_warns_when_no_instances(
    test_settings: object, caplog: pytest.LogCaptureFixture
) -> None:
    """App lifespan logs warning when no instances are configured."""
    assert test_settings is not None
    caplog.set_level(logging.WARNING)

    app = create_app()
    with TestClient(app, raise_server_exceptions=True):
        pass

    messages = [record.getMessage() for record in caplog.records]
    assert any("No instances configured" in message for message in messages)


@pytest.fixture(autouse=True)
def _ignore_ambient_tzdir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide any TZDIR the developer's own machine exports.

    A redirected TZDIR makes the timezone check bail out early, so an
    inherited value would turn the warning assertions below into no-ops.
    """
    monkeypatch.delenv("TZDIR", raising=False)


def _timezone_warnings(caplog: pytest.LogCaptureFixture, tz: str | None) -> list[str]:
    """Boot the app under *tz* and return the warnings about the timezone.

    Both tests below select rows the same way.  Keying the positive case on
    the message text and the negative one on a different fragment would let a
    reword quietly turn the negative assertion into a tautology, so the
    ``TZ=`` prefix the warning is built from is the single anchor.
    """
    caplog.set_level(logging.WARNING)
    with libc_timezone(tz), TestClient(create_app(), raise_server_exceptions=True):
        pass
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.getMessage().startswith("TZ=")
    ]


def test_startup_warns_when_tz_has_no_zone_file(
    test_settings: object, caplog: pytest.LogCaptureFixture
) -> None:
    """A TZ Houndarr cannot load is named rather than silently becoming UTC."""
    assert test_settings is not None

    warnings = _timezone_warnings(caplog, "Not/AZone")

    assert len(warnings) == 1
    assert "Not/AZone" in warnings[0]
    # The operator needs to know what to do about it, not only that it happened.
    assert "America/New_York" in warnings[0]


@pytest.mark.parametrize("tz", ["UTC", "Etc/UTC", None])
def test_startup_is_quiet_for_a_resolvable_tz(
    test_settings: object, caplog: pytest.LogCaptureFixture, tz: str | None
) -> None:
    """A working TZ, or none at all, must not warn on every boot.

    libc_timezone rather than monkeypatch.setenv: glibc keeps the zone it
    parsed at first use until tzset runs, so setting the variable alone
    compares zoneinfo against the host's real offset and warns about UTC on
    any Linux box that is not already on UTC.
    """
    assert test_settings is not None

    assert _timezone_warnings(caplog, tz) == []


def test_periodic_retention_runs_during_uptime(
    test_settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention purger runs at startup and periodically while app is running."""
    assert test_settings is not None
    from houndarr import app as app_module

    purges: list[int] = []

    async def _fake_purge_old_logs(retention_days: int) -> int:
        purges.append(retention_days)
        return 0

    async def _fake_start(self: Supervisor) -> None:
        return None

    async def _fake_stop(self: Supervisor) -> None:
        return None

    monkeypatch.setattr(app_module, "purge_old_logs", _fake_purge_old_logs)
    monkeypatch.setattr(app_module, "_LOG_RETENTION_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(Supervisor, "start", _fake_start)
    monkeypatch.setattr(Supervisor, "stop", _fake_stop)

    app = create_app()
    with TestClient(app, raise_server_exceptions=True):
        time.sleep(0.07)

    # At least one startup purge + at least one periodic purge.
    assert len(purges) >= 2


async def test_lifespan_fails_when_css_bundle_missing(
    test_settings: object,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifespan refuses to start if `app.built.css` is missing.

    Reproduces the failure mode that broke from-source installs in
    v1.10.0 (#481): the compiled CSS bundle is gitignored and only
    produced by the `pnpm run build-css` step (run automatically by
    the Docker css-build stage).  Without it the app booted into a
    silently-broken state where every page 404'd on the bundle.  The
    preflight in `_lifespan` now fails fast with an actionable log
    line pointing at the build command and the install guide.

    Drives the lifespan via ``app.router.lifespan_context`` rather
    than ``TestClient`` because Starlette's ``TestClient`` cleanup
    can deadlock on Linux when startup raises before the lifespan
    yield (the in-thread portal waits for a shutdown signal that
    never arrives).
    """
    assert test_settings is not None

    real_is_file = Path.is_file

    def _missing_css_only(self: Path) -> bool:
        if self.name == "app.built.css":
            return False
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", _missing_css_only)
    caplog.set_level(logging.CRITICAL)

    app = create_app()
    with pytest.raises(RuntimeError, match="Compiled CSS bundle missing"):
        async with app.router.lifespan_context(app):
            pass

    messages = [record.getMessage() for record in caplog.records]
    assert any("pnpm run build-css" in m for m in messages)
    assert any("from-source.md" in m for m in messages)


async def test_lifespan_succeeds_when_css_bundle_present(
    test_settings: object,
) -> None:
    """Sanity check: the happy-path (bundle present) startup works.

    Pinned alongside the failure-path test so a future refactor that
    breaks the preflight check still exercises both branches.  Drives
    the lifespan directly to mirror the failure-path test and avoid
    the TestClient cleanup deadlock the failing case can trigger.
    """
    assert test_settings is not None

    app = create_app()
    async with app.router.lifespan_context(app):
        # Reaching the yield means the preflight passed.
        pass

"""Tests for the GitHub update-check service.

The service is gated behind the ``update_check_enabled`` setting. Any
test that exercises the network path flips it on via ``set_enabled``;
tests for the disabled-path use ``respx`` assertions to prove no call
was made even when the cache window is expired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from houndarr.repositories.settings import get_setting, set_setting
from houndarr.services import update_check as uc

_LATEST_BODY = {
    "tag_name": "v2.0.0",
    "html_url": "https://github.com/av1155/houndarr/releases/tag/v2.0.0",
    "published_at": "2026-05-01T12:00:00Z",
    "prerelease": False,
    "draft": False,
}


def _url(repo: str = "av1155/houndarr") -> str:
    return f"https://api.github.com/repos/{repo}/releases/latest"


# ---------------------------------------------------------------------------
# Enabled/disabled toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_disabled_never_issues_http(db: None) -> None:
    """If the toggle is off, no background poll reaches github.com even
    when the cache is empty."""
    route = respx.get(_url()).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    status = await uc.get_update_status(force=False)
    assert not status.enabled
    assert status.latest_version is None
    assert not route.called


@pytest.mark.asyncio()
@respx.mock
async def test_manual_check_runs_even_when_disabled(db: None) -> None:
    """Check-now (force=True) fires the request regardless of the toggle
    so admins who keep auto-check off still have a one-off button."""
    route = respx.get(_url()).mock(
        return_value=httpx.Response(200, json=_LATEST_BODY, headers={"ETag": 'W/"manual"'})
    )
    # Toggle stays off.
    status = await uc.get_update_status(force=True)

    assert route.called
    assert not status.enabled
    assert status.latest_version == "2.0.0"
    assert status.checked_at is not None


@pytest.mark.asyncio()
@respx.mock
async def test_load_cached_status_never_issues_http(db: None) -> None:
    """``load_cached_status`` is the hook used by the preferences endpoint
    to re-render the row; it must never block on github.com even when
    the toggle is on and the cache is stale."""
    await uc.set_enabled(True)
    stale = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
    await set_setting(uc.KEY_LAST_AT, stale)
    await set_setting(uc.KEY_LATEST_VERSION, "1.9.0")

    route = respx.get(_url()).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    status = await uc.load_cached_status()

    assert not route.called
    assert status.enabled
    assert status.latest_version == "1.9.0"


@pytest.mark.asyncio()
@respx.mock
async def test_enabled_fetches_and_persists(db: None) -> None:
    """First run with the toggle on hits the API and persists the
    parsed latest release."""
    respx.get(_url()).mock(
        return_value=httpx.Response(
            200,
            json=_LATEST_BODY,
            headers={"ETag": 'W/"abc123"'},
        )
    )
    await uc.set_enabled(True)

    status = await uc.get_update_status(force=False)

    assert status.enabled
    assert status.latest_version == "2.0.0"
    assert status.release_url == _LATEST_BODY["html_url"]
    assert status.published_at == _LATEST_BODY["published_at"]
    assert status.checked_at is not None
    assert status.last_error_at is None
    assert await get_setting(uc.KEY_ETAG) == 'W/"abc123"'


# ---------------------------------------------------------------------------
# Caching windows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_cache_hit_within_24h_skips_http(db: None) -> None:
    """A recent successful check short-circuits the next call without
    touching the network."""
    await uc.set_enabled(True)
    recent = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    await set_setting(uc.KEY_LAST_AT, recent)
    await set_setting(uc.KEY_LATEST_VERSION, "1.9.0")
    await set_setting(uc.KEY_RELEASE_URL, "https://example.test/1.9.0")

    route = respx.get(_url()).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    status = await uc.get_update_status(force=False)

    assert not route.called
    assert status.latest_version == "1.9.0"


@pytest.mark.asyncio()
@respx.mock
async def test_cache_expiry_refreshes(db: None) -> None:
    """Once the 24h window elapses, the next call hits the API again."""
    await uc.set_enabled(True)
    stale = (datetime.now(tz=UTC) - timedelta(hours=25)).isoformat()
    await set_setting(uc.KEY_LAST_AT, stale)

    route = respx.get(_url()).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    status = await uc.get_update_status(force=False)

    assert route.called
    assert status.latest_version == "2.0.0"


# ---------------------------------------------------------------------------
# Manual refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_manual_refresh_always_hits_github(db: None) -> None:
    """Manual refresh has no client-side throttle: every click fires
    the request so the UI stays in sync with what the user just
    asked for. The button's own ``hx-disable-elt`` guards against
    in-flight double-submit, and GitHub's 60 req/hr/IP budget plus
    the ETag handshake handle the real ceiling."""
    await uc.set_enabled(True)
    await set_setting(uc.KEY_LATEST_VERSION, "1.9.0")
    # Recent cache that a ``force=False`` call would honour:
    fresh = (datetime.now(tz=UTC) - timedelta(minutes=2)).isoformat()
    await set_setting(uc.KEY_LAST_AT, fresh)

    route = respx.get(_url()).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    status = await uc.get_update_status(force=True)

    assert route.called
    assert status.latest_version == "2.0.0"


# ---------------------------------------------------------------------------
# ETag / 304 handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_etag_on_304_keeps_cached_version(db: None) -> None:
    """304 preserves the stored tag and advances last_at timestamp."""
    await uc.set_enabled(True)
    await set_setting(uc.KEY_LATEST_VERSION, "2.0.0")
    await set_setting(uc.KEY_RELEASE_URL, _LATEST_BODY["html_url"])
    await set_setting(uc.KEY_ETAG, 'W/"cached"')
    stale = (datetime.now(tz=UTC) - timedelta(hours=25)).isoformat()
    await set_setting(uc.KEY_LAST_AT, stale)
    await set_setting(uc.KEY_LAST_ERROR_AT, stale)

    respx.get(_url()).mock(return_value=httpx.Response(304))

    status = await uc.get_update_status(force=False)

    assert status.latest_version == "2.0.0"
    assert status.last_error_at is None  # cleared on successful 304
    assert status.checked_at is not None


# ---------------------------------------------------------------------------
# Network failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_network_error_preserves_cache(db: None) -> None:
    """Timeouts or transport errors do not clobber the cached release."""
    await uc.set_enabled(True)
    await set_setting(uc.KEY_LATEST_VERSION, "1.9.0")
    await set_setting(uc.KEY_RELEASE_URL, "https://example.test/1.9.0")
    stale = (datetime.now(tz=UTC) - timedelta(hours=25)).isoformat()
    await set_setting(uc.KEY_LAST_AT, stale)

    respx.get(_url()).mock(side_effect=httpx.ConnectTimeout("boom"))

    status = await uc.get_update_status(force=False)

    assert status.latest_version == "1.9.0"
    assert status.last_error_at is not None


@pytest.mark.asyncio()
@respx.mock
async def test_non_200_non_304_is_treated_as_error(db: None) -> None:
    """5xx responses are logged and preserve cached state."""
    await uc.set_enabled(True)
    await set_setting(uc.KEY_LATEST_VERSION, "1.9.0")
    stale = (datetime.now(tz=UTC) - timedelta(hours=25)).isoformat()
    await set_setting(uc.KEY_LAST_AT, stale)

    respx.get(_url()).mock(return_value=httpx.Response(503, text="Service Unavailable"))

    status = await uc.get_update_status(force=False)

    assert status.latest_version == "1.9.0"
    assert status.last_error_at is not None


# ---------------------------------------------------------------------------
# update_available derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_update_available_is_true_when_latest_is_newer(db: None) -> None:
    await uc.set_enabled(True)
    await set_setting(uc.KEY_LATEST_VERSION, "999.0.0")
    await set_setting(uc.KEY_LAST_AT, datetime.now(tz=UTC).isoformat())

    status = await uc.get_update_status(force=False)

    assert status.update_available is True


@pytest.mark.asyncio()
async def test_update_available_false_when_installed_matches(db: None) -> None:
    await uc.set_enabled(True)
    from houndarr import __version__ as installed

    await set_setting(uc.KEY_LATEST_VERSION, installed)
    await set_setting(uc.KEY_LAST_AT, datetime.now(tz=UTC).isoformat())

    status = await uc.get_update_status(force=False)

    assert status.update_available is False


@pytest.mark.asyncio()
async def test_update_available_false_for_dev_build_newer_than_latest(db: None) -> None:
    """Running a dev build that's ahead of the latest release does not
    read as 'downgrade available'."""
    await uc.set_enabled(True)
    await set_setting(uc.KEY_LATEST_VERSION, "0.0.1")
    await set_setting(uc.KEY_LAST_AT, datetime.now(tz=UTC).isoformat())

    status = await uc.get_update_status(force=False)

    assert status.update_available is False


# ---------------------------------------------------------------------------
# Custom repo via env var
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_repo_override_env_var(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOUNDARR_UPDATE_CHECK_REPO redirects the check to a different
    owner/repo without any code change."""
    from houndarr.config import bootstrap_settings

    monkeypatch.setenv("HOUNDARR_UPDATE_CHECK_REPO", "someone-else/fork")
    # Clear any pinned settings so get_settings re-reads the env var above.
    bootstrap_settings()

    respx.get(_url("someone-else/fork")).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    await uc.set_enabled(True)

    status = await uc.get_update_status(force=False)

    assert status.latest_version == "2.0.0"


@pytest.mark.asyncio()
@respx.mock
async def test_repo_override_invalid_falls_back_to_default(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed HOUNDARR_UPDATE_CHECK_REPO values fall back to the
    default upstream repo rather than sending a garbage path to the
    GitHub API."""
    from houndarr.config import bootstrap_settings

    # Missing slash, query-string injection, absolute URL: all rejected.
    monkeypatch.setenv("HOUNDARR_UPDATE_CHECK_REPO", "not-a-valid-slug")
    # Clear any pinned settings so get_settings re-reads the env var above.
    bootstrap_settings()

    # If the validator didn't fall back, the request would land on this
    # malformed URL and the default-repo mock would never be hit.
    respx.get(_url("av1155/houndarr")).mock(return_value=httpx.Response(200, json=_LATEST_BODY))
    await uc.set_enabled(True)

    status = await uc.get_update_status(force=False)

    assert status.latest_version == "2.0.0"


@pytest.mark.asyncio()
@respx.mock
async def test_refuses_non_github_html_url(db: None) -> None:
    """A payload whose html_url is not on https://github.com/ is refused
    so a compromised upstream response cannot land a javascript: or
    data: URL in the Admin panel's release link."""
    poisoned = {
        **_LATEST_BODY,
        "html_url": "javascript:alert(1)",
    }
    respx.get(_url()).mock(return_value=httpx.Response(200, json=poisoned))

    await uc.set_enabled(True)
    status = await uc.get_update_status(force=True)

    # Refused: release URL must not have been persisted; error marker set.
    assert status.release_url is None
    assert status.latest_version is None
    last_error = await get_setting(uc.KEY_LAST_ERROR_AT)
    assert last_error != ""


def test_parse_version_handles_v_prefix() -> None:
    """GitHub emits ``v1.9.0`` tags; our own VERSION file is ``1.9.0``.
    Both must parse to the same tuple so the comparator doesn't flag a
    version mismatch purely on the prefix."""
    assert uc._parse_version_tuple("v1.9.0") == (1, 9, 0)
    assert uc._parse_version_tuple("1.9.0") == (1, 9, 0)
    assert uc._parse_version_tuple("V2.0.0") == (2, 0, 0)
    # Junk still rejected.
    assert uc._parse_version_tuple("1.9") is None
    assert uc._parse_version_tuple("v1.9-rc1") is None


# ---------------------------------------------------------------------------
# Toggle-off side effect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_set_enabled_false_clears_last_error(db: None) -> None:
    """Turning the check off clears any stale error so the panel
    doesn't show a warning when the toggle is already disabled."""
    prior_error = datetime.now(tz=UTC).isoformat()
    await set_setting(uc.KEY_LAST_ERROR_AT, prior_error)
    await set_setting(uc.KEY_ENABLED, "1")

    await uc.set_enabled(False)

    assert (await get_setting(uc.KEY_LAST_ERROR_AT)) == ""

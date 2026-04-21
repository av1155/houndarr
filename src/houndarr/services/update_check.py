"""GitHub release polling for the Updates admin panel.

Opt-in only. The toggle lives in the ``settings`` table
(``update_check_enabled``) and defaults to off on every install, so the
service never reaches out to github.com until an admin flips it on.

Network call reaches a single hard-coded endpoint: the GitHub Releases
API for the configured ``owner/repo`` (default ``av1155/houndarr``,
overridable via ``HOUNDARR_UPDATE_CHECK_REPO``). No user-controlled
URL construction, so there is no SSRF surface. The endpoint
``/releases/latest`` already excludes drafts and pre-releases server-
side, which matches our "stable releases only" product decision.

Cache + rate-limit behaviour, in priority order:

1. ``update_check_enabled == "0"`` -> never issue a request.
2. Manual refresh (``force=True``) is rate-limited to once per
   ``MANUAL_REFRESH_MIN_INTERVAL`` so an admin mashing the button can
   not burn through the unauthenticated 60 req/hr/IP bucket.
3. Background poll honours a ``BACKGROUND_CHECK_INTERVAL`` gap between
   successful checks; within that window we serve cached state.
4. Every outgoing request sends ``If-None-Match`` when we hold an ETag,
   so GitHub responds with 304 Not Modified for unchanged releases. The
   304 still lets us advance ``update_check_last_at`` without re-parsing
   the body.

On network failure (timeout, 5xx, invalid JSON) the cached state from
the last successful check is preserved and a warning is logged. The
admin panel surfaces "Last checked X ago" so the staleness is visible
without an error banner.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from houndarr import __version__
from houndarr.config import get_settings
from houndarr.database import get_setting, set_setting

logger = logging.getLogger(__name__)

# Settings keys. Namespaced under ``update_check_`` so they stay grouped
# in the key/value store and so a future grep picks them up together.
KEY_ENABLED = "update_check_enabled"
KEY_LAST_AT = "update_check_last_at"
KEY_ETAG = "update_check_etag"
KEY_LATEST_VERSION = "update_check_latest_version"
KEY_RELEASE_URL = "update_check_release_url"
KEY_PUBLISHED_AT = "update_check_published_at"
KEY_LAST_ERROR_AT = "update_check_last_error_at"
KEY_LAST_MANUAL_AT = "update_check_last_manual_at"

# Polling cadence. Background fires at most once per 24 h; manual
# refresh fires at most once per 15 min. Both are server-wide, not
# per-session, because the rate-limit budget lives on the egress IP.
BACKGROUND_CHECK_INTERVAL = timedelta(hours=24)
MANUAL_REFRESH_MIN_INTERVAL = timedelta(minutes=15)

# Network timeouts. GitHub usually answers in well under a second; the
# generous ceilings leave headroom for transient congestion without
# letting a single bad connection stall the admin partial for long.
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_USER_AGENT = f"Houndarr-UpdateCheck/{__version__}"


@dataclass(frozen=True)
class UpdateStatus:
    """Snapshot returned to the route/template layer.

    Attributes:
        enabled: Whether the check is turned on.
        installed_version: The running image's ``__version__``.
        latest_version: Most recent release tag seen on GitHub, without
            the leading ``v``. ``None`` until the first successful check.
        release_url: ``html_url`` of the latest release, for the
            "Latest on GitHub" link.
        published_at: ISO-8601 timestamp of the latest release's
            publication. ``None`` until first success.
        checked_at: When the cached result was obtained. ``None`` if
            the check is enabled but has never run yet.
        last_error_at: When the most recent attempt failed. Allows the
            UI to tell "never checked" apart from "check is stale".
        update_available: Convenience flag derived from version compare.
    """

    enabled: bool
    installed_version: str
    latest_version: str | None
    release_url: str | None
    published_at: str | None
    checked_at: datetime | None
    last_error_at: datetime | None
    update_available: bool


def _parse_version_tuple(value: str | None) -> tuple[int, int, int] | None:
    """Normalise a release tag (``v1.10.0`` or ``1.10.0``) to a tuple.

    Returns ``None`` for anything that does not match ``MAJOR.MINOR.PATCH``
    so a future pre-release or build-metadata tag can not sneak past the
    comparator and be interpreted as a downgrade.
    """
    if not value:
        return None
    clean = value.strip().lstrip("vV")
    parts = clean.split(".")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # ``fromisoformat`` in 3.11+ accepts the ``Z`` suffix but older
        # DBs may carry a value stored by this module, so normalise.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def is_enabled() -> bool:
    """Return whether the admin has turned the check on."""
    raw = await get_setting(KEY_ENABLED)
    return raw == "1"


async def set_enabled(enabled: bool) -> None:
    """Persist the toggle and clear a stale error so the next panel
    render does not display an error left over from before the check
    was turned off."""
    await set_setting(KEY_ENABLED, "1" if enabled else "0")
    if not enabled:
        await set_setting(KEY_LAST_ERROR_AT, "")


async def _load_status(*, enabled: bool) -> UpdateStatus:
    """Read the cached state from ``settings`` without issuing HTTP."""
    latest_version = await get_setting(KEY_LATEST_VERSION) or None
    release_url = await get_setting(KEY_RELEASE_URL) or None
    published_at = await get_setting(KEY_PUBLISHED_AT) or None
    checked_at = _parse_iso(await get_setting(KEY_LAST_AT))
    last_error_at = _parse_iso(await get_setting(KEY_LAST_ERROR_AT))

    installed_tuple = _parse_version_tuple(__version__)
    latest_tuple = _parse_version_tuple(latest_version)
    update_available = bool(
        latest_tuple is not None and installed_tuple is not None and latest_tuple > installed_tuple
    )

    return UpdateStatus(
        enabled=enabled,
        installed_version=__version__,
        latest_version=latest_version,
        release_url=release_url,
        published_at=published_at,
        checked_at=checked_at,
        last_error_at=last_error_at,
        update_available=update_available,
    )


def _should_fetch(*, last_at: datetime | None, force: bool) -> bool:
    """Decide whether an outgoing request is warranted right now.

    Background path re-fetches once the 24 h window has elapsed. Manual
    path short-circuits that window but still respects a 15 min floor
    enforced by ``_manual_allowed``.
    """
    if last_at is None:
        return True
    if force:
        return True
    return _now() - last_at >= BACKGROUND_CHECK_INTERVAL


async def _manual_allowed() -> bool:
    """Return whether a manual refresh would stay inside the rate limit."""
    last_manual = _parse_iso(await get_setting(KEY_LAST_MANUAL_AT))
    if last_manual is None:
        return True
    return _now() - last_manual >= MANUAL_REFRESH_MIN_INTERVAL


async def _fetch(
    repo: str, prior_etag: str | None
) -> tuple[int, dict[str, object] | None, str | None]:
    """Issue the GitHub Releases call.

    Returns ``(status_code, payload_or_None, etag_or_None)``. The caller
    is responsible for interpreting the status code: 200 means fresh
    payload, 304 means "use cached", anything else is an error.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
        # Pinning a specific API version stops surprise breaking changes
        # on the wire schema we parse below.
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if prior_etag:
        headers["If-None-Match"] = prior_etag

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        response = await client.get(url, headers=headers)

    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None:
        try:
            if int(remaining) < 10:
                logger.warning(
                    "GitHub rate-limit budget is low (remaining=%s) for update check",
                    remaining,
                )
        except ValueError:
            pass

    if response.status_code == 304:
        return 304, None, prior_etag
    if response.status_code != 200:
        return response.status_code, None, None

    try:
        payload = response.json()
    except json.JSONDecodeError:
        return 0, None, None

    return 200, payload, response.headers.get("ETag")


async def _run_check(*, force: bool) -> UpdateStatus:
    """Perform the HTTP call and persist results; called only after
    ``is_enabled()`` returns True and ``_should_fetch`` returns True."""
    repo = get_settings().update_check_repo
    prior_etag = await get_setting(KEY_ETAG) or None

    try:
        status, payload, etag = await _fetch(repo, prior_etag)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.warning("update_check: network error reaching github.com (%s)", exc)
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status(enabled=True)

    if status == 304:
        # No content change; still bump ``last_at`` so the UI shows
        # "checked N minutes ago" reflecting reality, and clear any
        # prior error so the panel doesn't lie about freshness.
        await set_setting(KEY_LAST_AT, _now().isoformat())
        await set_setting(KEY_LAST_ERROR_AT, "")
        if force:
            await set_setting(KEY_LAST_MANUAL_AT, _now().isoformat())
        return await _load_status(enabled=True)

    if status != 200 or not isinstance(payload, dict):
        logger.warning(
            "update_check: unexpected GitHub response (status=%s, payload_type=%s)",
            status,
            type(payload).__name__,
        )
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status(enabled=True)

    tag_raw = payload.get("tag_name")
    html_url = payload.get("html_url")
    published = payload.get("published_at")
    if not isinstance(tag_raw, str) or not isinstance(html_url, str):
        logger.warning("update_check: release payload missing tag_name or html_url")
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status(enabled=True)

    normalized_tag = tag_raw.lstrip("vV")
    await set_setting(KEY_LATEST_VERSION, normalized_tag)
    await set_setting(KEY_RELEASE_URL, html_url)
    await set_setting(KEY_PUBLISHED_AT, published if isinstance(published, str) else "")
    await set_setting(KEY_LAST_AT, _now().isoformat())
    await set_setting(KEY_LAST_ERROR_AT, "")
    if etag:
        await set_setting(KEY_ETAG, etag)
    if force:
        await set_setting(KEY_LAST_MANUAL_AT, _now().isoformat())

    return await _load_status(enabled=True)


async def get_update_status(*, force: bool = False) -> UpdateStatus:
    """Return the current update-check snapshot, fetching from GitHub
    only when the cache window has expired (or ``force=True``).

    ``force=True`` is gated by ``_manual_allowed`` so callers that
    route user input (the Refresh button) can not bypass the 15 min
    floor just by passing the flag.
    """
    enabled = await is_enabled()
    if not enabled:
        return await _load_status(enabled=False)

    if force and not await _manual_allowed():
        # Refresh spammed: return cached state, leave rate-limit record
        # alone so the existing 15 min window keeps counting down.
        return await _load_status(enabled=True)

    last_at = _parse_iso(await get_setting(KEY_LAST_AT))
    if not _should_fetch(last_at=last_at, force=force):
        return await _load_status(enabled=True)

    return await _run_check(force=force)

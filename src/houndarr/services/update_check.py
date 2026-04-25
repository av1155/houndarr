"""GitHub release polling for the Updates admin panel.

Opt-in only. The toggle lives in the ``settings`` table
(``update_check_enabled``) and defaults to off on every install, so the
service never reaches out to github.com until an admin flips it on.

Network call reaches a single hard-coded endpoint: the GitHub Releases
API for the configured ``owner/repo`` (default ``av1155/houndarr``,
overridable via ``HOUNDARR_UPDATE_CHECK_REPO`` and validated against a
conservative slug regex in :mod:`houndarr.config`). No user-controlled
URL construction, so there is no SSRF surface. The endpoint
``/releases/latest`` already excludes drafts and pre-releases server-
side, which matches our "stable releases only" product decision.

This service runs **on demand** rather than on a timer. There is no
scheduled asyncio task; each GET ``/settings/admin/update-check``
invocation triggered by the Admin > Updates panel's ``hx-trigger="load"``
calls :func:`get_update_status` which decides whether to hit the wire.
Admins who never open Settings never cause a request to leave the
container.

Cache + rate-limit behaviour:

* When disabled (``update_check_enabled == "0"``), :func:`get_update_status`
  with ``force=False`` short-circuits and never issues an HTTP request.
* On-demand checks honour a ``BACKGROUND_CHECK_INTERVAL`` gap (24h) between
  successful checks so repeated Settings renders serve cached state instead
  of spamming the GitHub API.
* Manual refresh (``force=True``) bypasses the window and always hits the
  wire. There is no client-side throttle beyond HTMX's ``hx-disable-elt``
  on the button, which prevents in-flight double-submission; GitHub's own
  60 req/hr/IP unauthenticated budget is the real ceiling, and the
  ``If-None-Match`` / 304 handshake below keeps it from draining on
  unchanged releases.
* Every outgoing request sends ``If-None-Match`` when we hold an ETag,
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
from houndarr.repositories.settings import get_setting, set_setting

logger = logging.getLogger(__name__)

# Namespaced under ``update_check_`` so a future grep finds them together.
KEY_ENABLED = "update_check_enabled"
KEY_LAST_AT = "update_check_last_at"
KEY_ETAG = "update_check_etag"
KEY_LATEST_VERSION = "update_check_latest_version"
KEY_RELEASE_URL = "update_check_release_url"
KEY_PUBLISHED_AT = "update_check_published_at"
KEY_LAST_ERROR_AT = "update_check_last_error_at"

BACKGROUND_CHECK_INTERVAL = timedelta(hours=24)

# Generous ceiling so a slow connection does not stall the admin partial;
# GitHub typically answers well under a second.
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_USER_AGENT = f"Houndarr-UpdateCheck/{__version__}"


@dataclass(frozen=True, slots=True)
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
        # ``Z`` suffix comes from GitHub's ``published_at`` payload; our
        # own writes use ``+00:00``. Normalise both.
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


async def _load_status() -> UpdateStatus:
    """Read the cached state from ``settings`` without issuing HTTP.

    ``enabled`` is read inside the helper so callers never pass a stale
    flag. The manual-check path can flip the toggle and still render a
    correct snapshot in the same request.
    """
    enabled = await is_enabled()
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


async def load_cached_status() -> UpdateStatus:
    """Return the current snapshot without issuing HTTP.

    The preferences endpoint uses this to re-render the status row
    immediately after a toggle flip: blocking on a github.com round-
    trip inside the POST response would freeze the switch animation
    for up to 10s on a slow network.
    """
    return await _load_status()


def _should_fetch(*, last_at: datetime | None) -> bool:
    """Decide whether an on-demand check should hit the wire right now.

    Manual (``force=True``) clicks bypass this entirely and always hit
    the wire, so only the on-demand cadence (debounced by the 24h window)
    needs a check here.
    """
    if last_at is None:
        return True
    return _now() - last_at >= BACKGROUND_CHECK_INTERVAL


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
    reset_epoch = response.headers.get("x-ratelimit-reset")
    if remaining is not None:
        try:
            if int(remaining) < 10:
                # Include the reset timestamp so operators know how long
                # the budget is constrained. The 304 handshake means a
                # normal install rarely touches this path; when it does,
                # the log line should be actionable without a second lookup.
                try:
                    reset_iso = (
                        datetime.fromtimestamp(int(reset_epoch), tz=UTC).isoformat()
                        if reset_epoch is not None
                        else "unknown"
                    )
                except (ValueError, OverflowError, OSError):
                    reset_iso = "unknown"
                logger.warning(
                    "GitHub rate-limit budget is low for update check (remaining=%s reset_at=%s)",
                    remaining,
                    reset_iso,
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


async def _run_check() -> UpdateStatus:
    """Perform the HTTP call and persist results.

    Reached through two paths: the background poll (entered only after
    ``is_enabled()`` + ``_should_fetch`` agree) and the manual
    Check-now button (runs regardless of toggle and cache age).
    """
    repo = get_settings().update_check_repo
    prior_etag = await get_setting(KEY_ETAG) or None

    try:
        status, payload, etag = await _fetch(repo, prior_etag)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.warning("update_check: network error reaching github.com (%s)", exc)
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status()

    if status == 304:
        # No content change; still bump ``last_at`` so the UI shows
        # "checked N minutes ago" reflecting reality, and clear any
        # prior error so the panel doesn't lie about freshness.
        await set_setting(KEY_LAST_AT, _now().isoformat())
        await set_setting(KEY_LAST_ERROR_AT, "")
        return await _load_status()

    if status != 200 or not isinstance(payload, dict):
        logger.warning(
            "update_check: unexpected GitHub response (status=%s, payload_type=%s)",
            status,
            type(payload).__name__,
        )
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status()

    tag_raw = payload.get("tag_name")
    html_url = payload.get("html_url")
    published = payload.get("published_at")
    if not isinstance(tag_raw, str) or not isinstance(html_url, str):
        logger.warning("update_check: release payload missing tag_name or html_url")
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status()

    # html_url is rendered verbatim into an <a href="..."> in the Admin
    # panel. Jinja autoescape handles HTML chars but not URL schemes, so
    # an upstream response containing a javascript: URL would execute on
    # click. GitHub always hands back https://github.com/..., but treat
    # the payload as untrusted and refuse anything else.
    if not html_url.startswith("https://github.com/"):
        logger.warning(
            "update_check: release payload html_url %r does not start with "
            "https://github.com/; refusing to persist",
            html_url,
        )
        await set_setting(KEY_LAST_ERROR_AT, _now().isoformat())
        return await _load_status()

    normalized_tag = tag_raw.lstrip("vV")
    await set_setting(KEY_LATEST_VERSION, normalized_tag)
    await set_setting(KEY_RELEASE_URL, html_url)
    await set_setting(KEY_PUBLISHED_AT, published if isinstance(published, str) else "")
    await set_setting(KEY_LAST_AT, _now().isoformat())
    await set_setting(KEY_LAST_ERROR_AT, "")
    if etag:
        await set_setting(KEY_ETAG, etag)

    return await _load_status()


async def get_update_status(*, force: bool = False) -> UpdateStatus:
    """Return the current update-check snapshot, fetching when warranted.

    ``force=False`` (background poll) runs only when the toggle is on
    and the 24 h cache window has elapsed. ``force=True`` (manual
    Check-now) hits the wire unconditionally: the button is a direct
    user action and the ETag handshake keeps repeated clicks cheap.
    """
    if force:
        return await _run_check()

    if not await is_enabled():
        return await _load_status()

    last_at = _parse_iso(await get_setting(KEY_LAST_AT))
    if not _should_fetch(last_at=last_at):
        return await _load_status()

    return await _run_check()

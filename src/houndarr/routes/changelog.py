"""Changelog modal routes: auto-open popup, dismiss, disable, preferences toggle.

Four endpoints, all authenticated (handled by ``AuthMiddleware``):

- ``GET  /settings/changelog/popup`` — returns the modal HTML partial if the
  running version is newer than ``changelog_last_seen_version`` and popups
  are not disabled; otherwise returns an empty placeholder div.  Supports
  ``?force=1`` to bypass the decision and always render (used by the
  Settings page "Show last changelog" button).
- ``POST /settings/changelog/dismiss`` — writes ``changelog_last_seen_version``
  to the running version.  Idempotent.
- ``POST /settings/changelog/disable`` — writes the last-seen marker AND
  ``changelog_popups_disabled = "1"``.
- ``POST /settings/changelog/preferences`` — toggles
  ``changelog_popups_disabled`` from the Settings page; re-renders the
  Settings section partial.
"""

from __future__ import annotations

import re
from html import escape
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from markupsafe import Markup

from houndarr import __version__
from houndarr.database import get_setting, set_setting
from houndarr.routes._templates import get_templates
from houndarr.services.changelog import (
    ReleaseEntry,
    get_changelog,
    releases_between,
    should_show,
)

router = APIRouter(prefix="/settings/changelog", tags=["changelog"])

_GITHUB_ISSUES_URL = "https://github.com/av1155/houndarr/issues"

# Order: matches newline lookups that would otherwise be eaten by the
# escaping pass.  We escape the whole bullet first, then re-introduce the
# known-safe subset of markdown back in (inline code, bold, links, issue
# refs).  This keeps the vocabulary bounded and avoids pulling in a full
# markdown library for a closed set of patterns.
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
# URL pattern allows one level of nested parentheses so links like
# https://en.wikipedia.org/wiki/Foo_(bar) render correctly.
_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:[^()]|\([^)]*\))+)\)")
_ISSUE_REF_RE = re.compile(r"\(#(\d+)\)")

# Defense-in-depth: only accept these URL schemes inside [text](url).
# CHANGELOG.md is maintainer-authored and bundled into the image so the
# trust boundary covers this, but the allowlist prevents javascript:,
# data:, vbscript: from ever reaching an href even if a future entry
# contains an unsafe link.  Schemeless / fragment / relative URLs are
# allowed (no colon → no scheme).
_ALLOWED_URL_SCHEMES = ("http://", "https://", "mailto:", "/", "#")


def _is_safe_url(url: str) -> bool:
    """Return True if *url* is safe to put in an href."""
    lowered = url.strip().lower()
    if any(lowered.startswith(s) for s in _ALLOWED_URL_SCHEMES):
        return True
    # Schemeless URLs (no colon before the first slash/hash/end) are safe.
    colon = lowered.find(":")
    if colon == -1:
        return True
    slash = lowered.find("/")
    return slash != -1 and slash < colon


def _link_substitution(match: re.Match[str]) -> str:
    """Return either an <a> tag or the original text if the URL is unsafe."""
    text, url = match.group(1), match.group(2)
    if not _is_safe_url(url):
        # Re-emit the original markdown so the raw text is visible but
        # not clickable.  The captures came from already-escape()d text.
        return f"[{text}]({url})"
    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'class="text-brand-300 hover:text-brand-200 underline underline-offset-2">{text}</a>'
    )


def _render_changelog_bullet(raw: str) -> Markup:
    """Return a safe HTML fragment for a single CHANGELOG bullet.

    Escapes the input, then re-applies the small vocabulary actually used
    in ``CHANGELOG.md``: `` `inline code` ``, ``**bold**``, ``[text](url)``,
    and ``(#123)`` issue references (linked to GitHub).
    """
    safe = escape(raw)
    safe = _INLINE_CODE_RE.sub(r'<code class="text-brand-300">\1</code>', safe)
    safe = _BOLD_RE.sub(r"<strong>\1</strong>", safe)
    # Link targets are HTML-escaped by the preceding escape() pass, so the
    # URL is already safe for insertion into an href attribute.  The
    # callback also gates on a scheme allowlist so javascript:/data:
    # URLs never reach an href even if CHANGELOG.md contains them.
    safe = _LINK_RE.sub(_link_substitution, safe)
    safe = _ISSUE_REF_RE.sub(
        (
            r'(<a href="' + _GITHUB_ISSUES_URL + r'/\1" target="_blank" rel="noopener noreferrer" '
            r'class="text-brand-300 hover:text-brand-200 underline underline-offset-2">#\1</a>)'
        ),
        safe,
    )
    # Input was escape()d first, then a closed vocabulary of markdown patterns
    # (code, bold, links, issue refs) was re-applied with escaped capture groups.
    # No user-authored raw HTML reaches the template context.
    return Markup(safe)  # noqa: S704  # nosec B704


def _empty_slot_response() -> HTMLResponse:
    """Return a no-op placeholder that replaces ``#changelog-slot`` without a trigger."""
    return HTMLResponse(
        content='<div id="changelog-slot" aria-hidden="true"></div>',
        status_code=200,
    )


def _range_label(releases: list[ReleaseEntry], *, manual: bool, last_seen: str | None) -> str:
    """Build the modal's subtitle.

    Only returns a non-empty label when it adds information beyond what the
    first release heading already shows.  Single-release renders (manual
    re-open, pre-feature catch-up) suppress the subtitle entirely.
    """
    if manual or len(releases) < 2:
        return ""
    if last_seen:
        return f"Since v{last_seen}"
    return ""


@router.get("/popup", response_class=HTMLResponse)
async def popup(request: Request, force: int = 0) -> HTMLResponse:
    """Return the modal partial or an empty placeholder div.

    When ``force=0`` (auto-open flow), the decision respects
    ``changelog_popups_disabled`` and the ``last_seen``/``running``
    comparison.  When ``force=1`` (manual re-open from Settings), always
    renders the modal with only the current running version's block.

    Silent tracking: when popups are disabled, the endpoint silently
    advances ``changelog_last_seen_version`` to the running version on
    every poll so that re-enabling popups later does not surface a
    backlog of releases the admin chose to skip.
    """
    last_seen = await get_setting("changelog_last_seen_version")
    disabled = (await get_setting("changelog_popups_disabled")) == "1"
    is_manual = force == 1

    if not is_manual and disabled:
        if last_seen != __version__:
            await set_setting("changelog_last_seen_version", __version__)
        return _empty_slot_response()

    if not is_manual and not should_show(
        last_seen=last_seen, running=__version__, disabled=disabled
    ):
        return _empty_slot_response()

    if is_manual:
        releases = releases_between(last_seen=None, running=__version__)
    else:
        releases = releases_between(last_seen=last_seen, running=__version__)

    if not releases:
        # Running version has no CHANGELOG entry (dev build, missing block).
        # Auto-open suppresses silently; manual open still returns empty so
        # the caller does not see a broken modal.
        return _empty_slot_response()

    newest = releases[0]
    older = releases[1:]

    response = get_templates().TemplateResponse(
        request=request,
        name="partials/changelog_modal.html",
        context={
            "releases": releases,
            "newest": newest,
            "older": older,
            "range_label": _range_label(releases, manual=is_manual, last_seen=last_seen),
            "manual": is_manual,
        },
    )
    # After-Swap fires once the <dialog> is actually in the DOM; plain
    # HX-Trigger fires before the swap, so getElementById would return null.
    response.headers["HX-Trigger-After-Swap"] = "houndarr-show-changelog"
    return response


@router.post("/dismiss", response_class=Response)
async def dismiss(request: Request) -> Response:
    """Persist ``changelog_last_seen_version = __version__``.  Returns 204."""
    await set_setting("changelog_last_seen_version", __version__)
    return Response(status_code=204)


@router.post("/disable", response_class=Response)
async def disable(request: Request) -> Response:
    """Persist both last-seen and disabled=1.  Returns 204."""
    await set_setting("changelog_last_seen_version", __version__)
    await set_setting("changelog_popups_disabled", "1")
    return Response(status_code=204)


@router.post("/preferences", response_class=Response)
async def preferences(
    request: Request,
    enabled: Annotated[str, Form()] = "",
) -> Response:
    """Toggle ``changelog_popups_disabled`` from the Settings page switch.

    Checkbox sends ``enabled=on`` when checked, omits the field when
    unchecked. Returns ``204 No Content`` so HTMX skips the swap (per
    the htmx-config in ``base.html``). The switch's CSS transition runs
    to completion on the in-place DOM element instead of being
    interrupted by an outerHTML replacement that would snap the thumb
    to its final position without animating.
    """
    new_disabled = "0" if enabled == "on" else "1"
    await set_setting("changelog_popups_disabled", new_disabled)
    return Response(status_code=204)


def _is_hx_request(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/full", response_class=HTMLResponse)
async def full(request: Request) -> HTMLResponse:
    """Render every parsed release from ``CHANGELOG.md`` on its own page.

    HX-aware: returns only the content partial when ``HX-Request: true``
    so shell navigation swaps cleanly into ``#app-content``; returns the
    full ``changelog_full.html`` wrapper otherwise.
    """
    releases = get_changelog()
    template_name = (
        "partials/pages/changelog_full_content.html"
        if _is_hx_request(request)
        else "changelog_full.html"
    )
    return get_templates().TemplateResponse(
        request=request,
        name=template_name,
        context={"releases": releases, "version": __version__},
    )

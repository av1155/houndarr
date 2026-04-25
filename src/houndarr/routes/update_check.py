"""Admin > Updates endpoints for the GitHub release check.

Three HTMX-friendly routes:

- ``GET  /settings/admin/update-check`` returns the inline status
  partial with ``show_result=False`` so reloads never re-show a stale
  one-shot manual-click message.
- ``POST /settings/admin/update-check/refresh`` forces a poll and
  returns the same partial with ``show_result=True`` so the outcome
  animates in under the button. The button uses ``hx-disabled-elt``
  for in-flight protection; no other client-side throttle.
- ``POST /settings/admin/update-check/preferences`` toggles
  ``update_check_enabled`` and returns the re-rendered partial from
  cache (no network) so the switch animation stays snappy.

Auth + CSRF are handled by ``AuthMiddleware`` for every route.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from houndarr.routes._templates import get_templates
from houndarr.services.update_check import (
    get_update_status,
    load_cached_status,
    set_enabled,
)

router = APIRouter(prefix="/settings/admin/update-check", tags=["update-check"])


def _timeago(value: datetime | None) -> str:
    """Render a UTC datetime as "N minutes ago" / "N hours ago" / "N days ago".

    Used by the Admin > Updates status partial to avoid pulling in a
    general-purpose humanize dependency for a single line of UI. Falls
    back to "just now" for sub-minute deltas so the row never reads
    "0 minutes ago" which looks broken.
    """
    if value is None:
        return ""
    now = datetime.now(tz=UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = now - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


@router.get("", response_class=HTMLResponse)
async def status(request: Request) -> HTMLResponse:
    """Return the inline status partial for the Admin > Updates panel.

    ``show_result=False`` so the ephemeral result message from a prior
    manual click does not reappear on page reload when auto-check is
    off; the row renders down to just the button + cached timestamp.
    """
    snapshot = await get_update_status(force=False)
    return get_templates().TemplateResponse(
        request=request,
        name="partials/admin/update_check_row.html",
        context={"s": snapshot, "show_result": False},
    )


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    """Force a GitHub re-poll.

    ``show_result=True`` so the result message is rendered under the
    button after a manual click even when auto-check is off. The
    message is ephemeral: the next GET (from a reload or navigation)
    lands with ``show_result=False`` and the message falls away.
    """
    snapshot = await get_update_status(force=True)
    return get_templates().TemplateResponse(
        request=request,
        name="partials/admin/update_check_row.html",
        context={"s": snapshot, "show_result": True},
    )


@router.post("/preferences", response_class=HTMLResponse)
async def preferences(
    request: Request,
    enabled: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Toggle ``update_check_enabled`` from the Admin > Updates switch.

    Returns the re-rendered status row so the slot reflects the new
    state without a page reload. The row is loaded from cache only
    (no github.com round-trip) so the switch animation stays snappy;
    when the user flips the toggle on with no cached data yet, the
    partial carries an ``hx-trigger`` that async-fires the first
    poll after the swap lands.
    """
    await set_enabled(enabled == "on")
    snapshot = await load_cached_status()
    return get_templates().TemplateResponse(
        request=request,
        name="partials/admin/update_check_row.html",
        context={"s": snapshot, "show_result": False},
    )

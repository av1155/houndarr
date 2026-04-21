"""Admin > Updates endpoints for the GitHub release check.

Three HTMX-friendly routes:

- ``GET  /settings/admin/update-check`` — renders the inline status
  row partial. The Updates panel issues this via
  ``hx-trigger="load once delay:200ms"`` so the rest of the card paints
  immediately and the check populates out of band.
- ``POST /settings/admin/update-check/refresh`` — forces a re-poll
  subject to the 15 min server-wide floor in
  :func:`houndarr.services.update_check._manual_allowed`, then swaps
  the partial back in.
- ``POST /settings/admin/update-check/preferences`` — toggles
  ``update_check_enabled`` from the switch. Returns 204 like the
  existing changelog preferences endpoint so HTMX leaves the switch
  animation alone.

Authentication is handled by ``AuthMiddleware`` (all routes require a
live session). CSRF is enforced by the same middleware on mutating
verbs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from houndarr.services.update_check import (
    get_update_status,
    set_enabled,
)

router = APIRouter(prefix="/settings/admin/update-check", tags=["update-check"])

_templates: Jinja2Templates | None = None


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


def _get_templates() -> Jinja2Templates:
    """Lazy-init the template loader.

    Matching the pattern used by other routes (changelog, settings)
    keeps module import cheap; the real cost only lands the first time
    a route renders. The ``timeago`` filter is registered here so the
    status partial can format ``checked_at`` without dragging in
    humanize as a dependency.
    """
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(
            directory=str(Path(__file__).resolve().parent.parent / "templates")
        )
        _templates.env.filters["timeago"] = _timeago
    return _templates


@router.get("", response_class=HTMLResponse)
async def status(request: Request) -> HTMLResponse:
    """Return the inline status partial for the Admin > Updates panel."""
    snapshot = await get_update_status(force=False)
    return _get_templates().TemplateResponse(
        request=request,
        name="partials/admin/update_check_row.html",
        context={"s": snapshot},
    )


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    """Force a GitHub re-poll (rate-limited to once per 15 min)."""
    snapshot = await get_update_status(force=True)
    return _get_templates().TemplateResponse(
        request=request,
        name="partials/admin/update_check_row.html",
        context={"s": snapshot},
    )


@router.post("/preferences", response_class=Response)
async def preferences(
    request: Request,
    enabled: Annotated[str, Form()] = "",
) -> Response:
    """Toggle ``update_check_enabled`` from the Admin > Updates switch.

    Checkbox posts ``enabled=on`` when checked, omits the field when
    unchecked. Returns ``204 No Content`` so HTMX keeps the switch's
    CSS transition from being interrupted by an outerHTML swap.
    """
    await set_enabled(enabled == "on")
    return Response(status_code=204)

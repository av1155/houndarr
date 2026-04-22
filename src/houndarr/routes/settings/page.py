"""GET /settings: the settings landing page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from houndarr.routes.settings._helpers import render_settings_page

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request) -> HTMLResponse:
    """Render the settings page with the current list of instances."""
    return await render_settings_page(request)

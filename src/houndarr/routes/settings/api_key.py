"""POST handlers for the Admin > API Key sub-section.

The Houndarr API key surface is rendered inline within the Settings
page's Admin parent dropdown as a sibling of Security / Updates /
Maintenance / Danger.  The two POST routes return the section partial
so HTMX can swap ``#admin-api-key`` outerHTML in place; generate
attaches ``HX-Trigger-After-Swap`` so the in-section reveal modal
opens once the new markup is in the DOM.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from houndarr.auth.houndarr_api_key import generate_api_key, hash_api_key
from houndarr.repositories import widget_api_key as widget_api_key_repo
from houndarr.routes._htmx import hx_trigger_after_swap
from houndarr.routes.settings._helpers import render
from houndarr.value_objects import WidgetApiKey

router = APIRouter()

_TEMPLATE = "partials/admin/api_key.html"
_REVEAL_EVENT = "houndarr-show-api-key"


def _render_section(
    request: Request,
    *,
    api_key: WidgetApiKey | None,
    plaintext_key: str | None = None,
) -> HTMLResponse:
    """Render the Admin > API Key sub-section partial."""
    response = render(
        request,
        _TEMPLATE,
        api_key=api_key,
        plaintext_key=plaintext_key,
    )
    if plaintext_key is not None:
        response.headers["Cache-Control"] = "no-store"
        hx_trigger_after_swap(response, _REVEAL_EVENT)
    return response


@router.post("/settings/api-key/generate", response_class=HTMLResponse)
async def api_key_generate(request: Request) -> HTMLResponse:
    """Generate or replace the active Houndarr API key."""
    plaintext_key = generate_api_key()
    stored_key = await widget_api_key_repo.set(hash_api_key(plaintext_key))
    return _render_section(request, api_key=stored_key, plaintext_key=plaintext_key)


@router.post("/settings/api-key/revoke", response_class=HTMLResponse)
async def api_key_revoke(request: Request) -> HTMLResponse:
    """Revoke the active Houndarr API key."""
    await widget_api_key_repo.revoke()
    return _render_section(request, api_key=None)

"""HTML page routes: setup, login, logout, dashboard."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from houndarr import __version__
from houndarr.auth import (
    check_credentials,
    check_login_rate_limit,
    clear_login_attempts,
    clear_session,
    create_session,
    is_setup_complete,
    normalize_username,
    record_failed_login,
    set_password,
    set_username,
    validate_username,
)
from houndarr.deps import get_master_key
from houndarr.repositories.settings import set_setting
from houndarr.routes._htmx import is_hx_request
from houndarr.routes._templates import get_templates
from houndarr.services.instances import list_instances

router = APIRouter()


def _render(
    request: Request,
    template_name: str,
    status_code: int = 200,
    **kwargs: object,
) -> HTMLResponse:
    """Render a Jinja2 template with common context variables.

    Injects ``csrf_token`` from the CSRF cookie so templates can embed it
    in hidden form fields for non-HTMX form submissions.
    """
    from houndarr.auth import CSRF_COOKIE_NAME

    csrf_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    context = {"version": __version__, "csrf_token": csrf_token, **kwargs}
    return get_templates().TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request) -> HTMLResponse:
    """Show the first-run password setup page."""
    if await is_setup_complete():
        return RedirectResponse(url="/login", status_code=302)  # type: ignore[return-value]
    return _render(request, "setup.html", show_nav=False)


@router.post("/setup", response_class=HTMLResponse)
async def setup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
) -> HTMLResponse:
    """Process the first-run password setup form."""
    if await is_setup_complete():
        return RedirectResponse(url="/login", status_code=302)  # type: ignore[return-value]

    username_error = validate_username(username)
    if username_error is not None:
        return _render(
            request,
            "setup.html",
            status_code=422,
            show_nav=False,
            error=username_error,
        )

    if len(password) < 8:
        return _render(
            request,
            "setup.html",
            status_code=422,
            show_nav=False,
            error="Password must be at least 8 characters.",
        )

    if password != password_confirm:
        return _render(
            request,
            "setup.html",
            status_code=422,
            show_nav=False,
            error="Passwords do not match.",
        )

    await set_username(normalize_username(username))
    await set_password(password)
    # Silently seed the changelog last-seen marker so fresh installs never
    # see the "What's new" modal on their first dashboard load.  Upgraders
    # (no stored value yet) fall through to the pre-feature catch-up path.
    await set_setting("changelog_last_seen_version", __version__)
    return RedirectResponse(url="/login", status_code=303)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    """Show the login page."""
    if not await is_setup_complete():
        return RedirectResponse(url="/setup", status_code=302)  # type: ignore[return-value]
    return _render(request, "login.html", show_nav=False)


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse:
    """Process login form."""
    if not await is_setup_complete():
        return RedirectResponse(url="/setup", status_code=302)  # type: ignore[return-value]

    if not check_login_rate_limit(request):
        return _render(
            request,
            "login.html",
            status_code=429,
            show_nav=False,
            error="Too many attempts. Please wait a moment.",
        )

    if not await check_credentials(username, password):
        record_failed_login(request)
        return _render(
            request,
            "login.html",
            status_code=401,
            show_nav=False,
            error="Invalid credentials.",
        )

    clear_login_attempts(request)
    response: RedirectResponse = RedirectResponse(url="/", status_code=303)
    await create_session(response)
    return response  # type: ignore[return-value]


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear session and redirect to login."""
    response = RedirectResponse(url="/login", status_code=303)
    clear_session(response)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Main dashboard page."""
    template_name = (
        "partials/pages/dashboard_content.html" if is_hx_request(request) else "dashboard.html"
    )
    return _render(request, template_name)


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


# Map from Instance.core.type.value to the CSS --inst-<slug> token
# suffix the Logs page cycle cards consume.  Collapses the two
# Whisparr variants to slugs Tailwind accepts without an underscore
# (--inst-whisparr / --inst-whisparr-v3).  Instances whose type is
# unknown to this map (future *arr families, legacy rows) are handled
# by the template's default in `instance_accent_by_name.get(name, '')`.
_INSTANCE_TYPE_TO_ACCENT_SLUG = {
    "sonarr": "sonarr",
    "radarr": "radarr",
    "lidarr": "lidarr",
    "readarr": "readarr",
    "whisparr_v2": "whisparr",
    "whisparr_v3": "whisparr-v3",
}


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    master_key: Annotated[bytes, Depends(get_master_key)],
    instance_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    search_kind: str | None = Query(default=None),
    cycle_trigger: str | None = Query(default=None),
    hide_system: str | None = Query(default=None),
    hide_skipped: str | None = Query(default=None),
) -> HTMLResponse:
    """Search log viewer page.

    Query parameters pre-apply filters so the dashboard's error banner
    and per-card error pill can deep-link straight to the relevant
    instance/action rows.
    """
    from houndarr.routes.api.logs import (
        parse_cycle_trigger,
        parse_hide_skipped,
        parse_hide_system,
        parse_instance_id,
        parse_search_kind,
    )
    from houndarr.services.log_query import (
        compute_load_more_limit,
        query_logs,
        summarize_rows,
    )

    try:
        parsed_instance_id = parse_instance_id(instance_id)
        parsed_search_kind = parse_search_kind(search_kind)
        parsed_cycle_trigger = parse_cycle_trigger(cycle_trigger)
        parsed_hide_system = parse_hide_system(hide_system) if hide_system is not None else True
        parsed_hide_skipped = (
            parse_hide_skipped(hide_skipped) if hide_skipped is not None else False
        )
    except HTTPException:
        # Malformed query string: fall back to unfiltered view so the
        # page still loads rather than bubbling a 422 JSON response.
        parsed_instance_id = None
        parsed_search_kind = None
        parsed_cycle_trigger = None
        parsed_hide_system = True
        parsed_hide_skipped = False

    parsed_action = action or None

    instances = await list_instances(master_key=master_key)
    rows = await query_logs(
        instance_id=parsed_instance_id,
        action=parsed_action,
        search_kind=parsed_search_kind,
        cycle_trigger=parsed_cycle_trigger,
        hide_system=parsed_hide_system,
        hide_skipped=parsed_hide_skipped,
        before=None,
        limit=50,
    )
    # `summary` is still consumed by the legacy table template; dropped
    # together with partials/log_summary.html in the redesign commit.
    summary = summarize_rows(rows)

    # Precompute the name -> accent-slug lookup the cycle-card template
    # uses to set --cycle-accent.  Doing it here (once, server-side)
    # avoids a per-row dict rebuild inside the Jinja loop.
    instance_accent_by_name = {
        inst.core.name: _INSTANCE_TYPE_TO_ACCENT_SLUG.get(inst.core.type.value, "")
        for inst in instances
    }

    template_name = "partials/pages/logs_content.html" if is_hx_request(request) else "logs.html"
    return _render(
        request,
        template_name,
        instances=instances,
        rows=rows,
        summary=summary,
        limit=50,
        load_more_limit=compute_load_more_limit(50),
        selected_instance_id=parsed_instance_id,
        selected_action=parsed_action,
        selected_search_kind=parsed_search_kind,
        selected_cycle_trigger=parsed_cycle_trigger,
        selected_hide_system=parsed_hide_system,
        selected_hide_skipped=parsed_hide_skipped,
        instance_id=parsed_instance_id,
        action=parsed_action,
        search_kind=parsed_search_kind,
        cycle_trigger=parsed_cycle_trigger,
        hide_system=parsed_hide_system,
        hide_skipped=parsed_hide_skipped,
        before=None,
        instance_accent_by_name=instance_accent_by_name,
    )


@router.get("/settings/help", response_class=HTMLResponse)
async def settings_help_page(request: Request) -> HTMLResponse:
    """Settings help page with guidance for instance controls."""
    template_name = (
        "partials/pages/settings_help_content.html"
        if is_hx_request(request)
        else "settings_help.html"
    )
    return _render(request, template_name)

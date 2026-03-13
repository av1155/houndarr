"""HTML page routes: setup, login, logout, dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
from houndarr.services.instances import list_instances

router = APIRouter()

# Templates are resolved relative to this file at runtime
_templates: Jinja2Templates | None = None


def get_templates() -> Jinja2Templates:
    global _templates  # noqa: PLW0603
    if _templates is None:
        from pathlib import Path

        _templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
    return _templates


def _render(
    request: Request,
    template_name: str,
    status_code: int = 200,
    **kwargs: object,
) -> HTMLResponse:
    """Render a Jinja2 template with common context variables."""
    context = {"version": __version__, **kwargs}
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
    return _render(request, "dashboard.html")


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request) -> HTMLResponse:
    """Search log viewer page — initial render with no filters applied."""
    from houndarr.routes.api.logs import _query_logs

    master_key: bytes = request.app.state.master_key
    instances = await list_instances(master_key=master_key)
    rows = await _query_logs(
        instance_id=None,
        action=None,
        search_kind=None,
        cycle_trigger=None,
        hide_system=True,
        before=None,
        limit=50,
    )
    return _render(
        request,
        "logs.html",
        instances=instances,
        rows=rows,
        limit=50,
        selected_instance_id=None,
        selected_action=None,
        selected_search_kind=None,
        selected_cycle_trigger=None,
        selected_hide_system=True,
        instance_id=None,
        action=None,
        search_kind=None,
        cycle_trigger=None,
        hide_system=True,
        before=None,
    )


@router.get("/settings/help", response_class=HTMLResponse)
async def settings_help_page(request: Request) -> HTMLResponse:
    """Settings help page with guidance for instance controls."""
    return _render(request, "settings_help.html")

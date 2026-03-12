"""HTML page routes: setup, login, logout, dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from houndarr import __version__
from houndarr.auth import (
    check_login_rate_limit,
    check_password,
    clear_login_attempts,
    clear_session,
    create_session,
    is_setup_complete,
    record_failed_login,
    set_password,
)

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
    password: str = Form(...),
    password_confirm: str = Form(...),
) -> HTMLResponse:
    """Process the first-run password setup form."""
    if await is_setup_complete():
        return RedirectResponse(url="/login", status_code=302)  # type: ignore[return-value]

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

    if not await check_password(password):
        record_failed_login(request)
        return _render(
            request,
            "login.html",
            status_code=401,
            show_nav=False,
            error="Incorrect password.",
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

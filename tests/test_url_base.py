"""Tests for serving Houndarr under ``HOUNDARR_URL_BASE``."""

from __future__ import annotations

import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from houndarr.config import _parse_url_base, bootstrap_settings
from tests.conftest import csrf_headers

_BASE = "/houndarr"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("/", ""),
        ("  ", ""),
        ("houndarr", "/houndarr"),
        ("/houndarr", "/houndarr"),
        ("/houndarr/", "/houndarr"),
        ("/apps/houndarr", "/apps/houndarr"),
        ("/hound-arr_1.0~x", "/hound-arr_1.0~x"),
    ],
)
def test_parse_url_base_normalises(raw: str, expected: str) -> None:
    assert _parse_url_base(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "/houndarr?x=1",
        "/houndarr#frag",
        "/hound arr",
        '/a"onload="x',
        "/a//b",
        "/../etc",
        "/a/./b",
        "https://example.com/houndarr",
        "/caf\u00e9",
    ],
)
def test_parse_url_base_rejects_unsafe_values(raw: str) -> None:
    assert _parse_url_base(raw) == ""


@pytest.fixture()
def base_app(tmp_data_dir: str) -> Generator[TestClient]:
    """TestClient for an app served under ``/houndarr``."""
    from houndarr.app import create_app
    from houndarr.auth import reset_auth_caches

    bootstrap_settings(data_dir=tmp_data_dir, url_base=_BASE)
    reset_auth_caches()
    with TestClient(create_app(), raise_server_exceptions=True) as client:
        yield client


def _setup_and_login(client: TestClient, prefix: str = _BASE) -> None:
    client.post(
        f"{prefix}/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
    )
    client.post(f"{prefix}/login", data={"username": "admin", "password": "ValidPass1!"})


def test_unauthenticated_redirect_keeps_base_path(base_app: TestClient) -> None:
    resp = base_app.get(f"{_BASE}/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_BASE}/setup"

    _setup_and_login(base_app)
    base_app.cookies.clear()
    resp = base_app.get(f"{_BASE}/settings", follow_redirects=False)
    assert resp.headers["location"] == f"{_BASE}/login"


def test_setup_login_logout_redirects_keep_base_path(base_app: TestClient) -> None:
    resp = base_app.post(
        f"{_BASE}/setup",
        data={
            "username": "admin",
            "password": "ValidPass1!",
            "password_confirm": "ValidPass1!",
        },
        follow_redirects=False,
    )
    assert resp.headers["location"] == f"{_BASE}/login"

    resp = base_app.post(
        f"{_BASE}/login",
        data={"username": "admin", "password": "ValidPass1!"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{_BASE}/"

    resp = base_app.post(f"{_BASE}/logout", follow_redirects=False)
    assert resp.headers["location"] == f"{_BASE}/login"


def test_bare_base_path_serves_dashboard(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    resp = base_app.get(_BASE, follow_redirects=False)
    assert resp.status_code == 200
    assert 'id="app-content"' in resp.text


def test_prefix_stripped_by_proxy_still_routes(base_app: TestClient) -> None:
    _setup_and_login(base_app, prefix="")
    resp = base_app.get("/logs", follow_redirects=False)
    assert resp.status_code == 200
    assert f'href="{_BASE}/logs"' in resp.text


def test_healthcheck_and_static_under_base_path(base_app: TestClient) -> None:
    assert base_app.get("/api/health").status_code == 200
    assert base_app.get(f"{_BASE}/api/health").status_code == 200

    resp = base_app.get(f"{_BASE}/static/js/app.js")
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


def test_rendered_urls_carry_base_path(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    html = base_app.get(f"{_BASE}/").text

    assert f'href="{_BASE}/static/css/app.built.css' in html
    assert f'src="{_BASE}/static/js/htmx.min.js' in html
    assert f'hx-get="{_BASE}/api/status"' in html
    assert f'action="{_BASE}/logout"' in html
    assert f'basePath: "{_BASE}"' in html
    assert not re.search(r'(?:href|src|action|hx-get|hx-post|hx-delete)="/(?!houndarr)', html)


def test_settings_partials_carry_base_path(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    html = base_app.get(f"{_BASE}/settings").text

    assert f'hx-get="{_BASE}/settings/instances/add-form"' in html
    assert f'hx-post="{_BASE}/settings/account/password"' in html
    assert not re.search(r'(?:href|src|action|hx-get|hx-post|hx-delete)="/(?!houndarr)', html)


def test_nav_active_state_ignores_base_path(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    html = base_app.get(f"{_BASE}/logs").text
    logs_link = re.search(r'<a [^>]*data-shell-route="/logs"[^>]*>', html)
    dash_link = re.search(r'<a [^>]*data-shell-route="/"[^>]*>', html)
    assert logs_link and "pill-nav__tab--active" in logs_link.group(0)
    assert dash_link and "pill-nav__tab--active" not in dash_link.group(0)


def test_factory_reset_redirect_keeps_base_path(
    base_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import houndarr.routes.admin as admin_routes

    async def _noop_reset(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(admin_routes, "factory_reset", _noop_reset)
    _setup_and_login(base_app)
    resp = base_app.post(
        f"{_BASE}/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "current_password": "ValidPass1!"},
        headers=csrf_headers(base_app),
    )
    assert resp.headers["HX-Redirect"] == f"{_BASE}/setup"


def test_default_app_has_no_prefix(app: TestClient) -> None:
    resp = app.get("/", follow_redirects=False)
    assert resp.headers["location"] == "/setup"
    assert 'href="/static/css/app.built.css' in app.get("/setup").text

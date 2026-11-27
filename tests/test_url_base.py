"""Tests for serving Houndarr under ``HOUNDARR_URL_BASE`` (#918)."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette._utils import get_route_path
from starlette.requests import Request
from starlette.routing import Mount

from houndarr.auth.houndarr_api_key import generate_api_key, hash_api_key
from houndarr.base_path import _path_only, _rebase, route_path
from houndarr.clients._wire_models import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.config import (
    _URL_BASE_RESERVED_SEGMENTS,
    AppSettings,
    _parse_url_base,
    _url_base_error,
    bootstrap_settings,
)
from houndarr.repositories import widget_api_key
from tests.conftest import csrf_headers

_BASE = "/houndarr"
_PASSWORD = "ValidPass1!"
_TRUSTED_IP = "172.18.0.5"
_AUTH_HEADER = "Remote-User"
_REPO_ROOT = Path(__file__).resolve().parents[1]

_SETUP_FORM = {"username": "admin", "password": _PASSWORD, "password_confirm": _PASSWORD}

_INSTANCE_FORM = {
    "name": "My Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "api_key": "test-api-key",
    "sonarr_search_mode": "episode",
    "connection_verified": "true",
}


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("/", ""),
        ("houndarr", "/houndarr"),
        ("/houndarr", "/houndarr"),
        ("/houndarr/", "/houndarr"),
        ("houndarr/", "/houndarr"),
        ("/apps/houndarr/", "/apps/houndarr"),
        ("/houndarr//", "/houndarr/"),
        ("/houndarr\n/", "/houndarr\n"),
    ],
)
def test_parse_url_base_only_normalises_slashes(raw: str, expected: str) -> None:
    assert _parse_url_base(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["", "/houndarr", "/apps/houndarr", "/hound-arr_1.0~x", "/apis", "/staticx", "/houndarr/api"],
)
def test_url_base_accepts_plain_paths(value: str) -> None:
    assert _url_base_error(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "/houndarr/",
        "//houndarr",
        "/a//b",
        "/.",
        "/..",
        "/../etc",
        "/a/./b",
        "/a/..",
        "/houndarr?x=1",
        "/houndarr#frag",
        "/hound arr",
        "/ houndarr",
        '/a"onload="x',
        "/a;b",
        "/a;Domain=evil.example",
        "/a%2fb",
        "/a\\b",
        "/a:b",
        "/a@b",
        "/houndarr\n",
        "/houndarr\r",
        "/houndarr\t",
        "/houndarr\x00",
        "/café",
        "https://example.com/houndarr",
    ],
)
def test_url_base_rejects_unsafe_values(value: str) -> None:
    error = _url_base_error(value)
    assert error is not None
    assert repr(value) in error


@pytest.mark.parametrize(
    "value",
    [
        "/api",
        "/API",
        "/static",
        "/Static/x",
        "/settings",
        "/settings/houndarr",
        "/logs",
        "/login",
        "/logout",
        "/setup",
        "/openapi.json",
    ],
)
def test_url_base_rejects_a_first_segment_houndarr_serves(value: str) -> None:
    error = _url_base_error(value)
    assert error is not None
    assert "already serves" in error


def _route_paths(routes: list[Any]) -> Iterator[str]:
    for route in routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            yield path
        elif not isinstance(route, Mount):
            # FastAPI wraps an included router; its routes already carry full paths.
            yield from _route_paths(getattr(getattr(route, "original_router", None), "routes", []))


def test_reserved_segments_cover_every_route(tmp_data_dir: str) -> None:
    """A new top-level route must join the denylist, or a base could swallow it."""
    from houndarr.app import create_app

    bootstrap_settings(data_dir=tmp_data_dir, dev=True)
    try:
        app = create_app()
    finally:
        bootstrap_settings()
    paths = set(_route_paths(app.routes))
    assert {"/api/health", "/settings", "/static"} <= paths
    first_segments = {path.split("/")[1] for path in paths if path != "/"}
    assert first_segments <= _URL_BASE_RESERVED_SEGMENTS


def test_invalid_url_base_is_a_fatal_config_error() -> None:
    errors = AppSettings(url_base=_parse_url_base("/houndarr\n/")).validate_auth_config()
    assert len(errors) == 1
    assert errors[0].startswith("HOUNDARR_URL_BASE must be a path")


@pytest.mark.parametrize(
    ("args", "env", "error"),
    [
        pytest.param(["--url-base", "/api"], {}, "HOUNDARR_URL_BASE", id="cli-route-segment"),
        pytest.param(
            [], {"HOUNDARR_URL_BASE": "/a;Domain=evil.example"}, "HOUNDARR_URL_BASE", id="env"
        ),
        # An open database pool keeps this exit from ending the process.
        pytest.param(["--auth-mode", "proxy"], {}, "HOUNDARR_AUTH_PROXY_HEADER", id="auth"),
    ],
)
def test_cli_exits_on_a_config_error(
    tmp_path: Path, args: list[str], env: dict[str, str], error: str
) -> None:
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HOUNDARR_", "PYTEST_"))
    }
    result = subprocess.run(
        [sys.executable, "-m", "houndarr", "--data-dir", str(tmp_path), *args],
        cwd=_REPO_ROOT,
        env={**clean_env, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert f"Configuration error: {error}" in output


# ---------------------------------------------------------------------------
# Middleware: path normalisation and route parity
# ---------------------------------------------------------------------------


def _scope(path: str, raw_path: bytes | None = None) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode() if raw_path is None else raw_path,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "scheme": "http",
    }


@pytest.mark.parametrize(
    ("path", "routed"),
    [
        ("/houndarr/settings", "/settings"),
        ("/settings", "/settings"),
        ("/houndarr", "/"),
        ("/houndarr/", "/"),
        ("/", "/"),
        ("//login", "/login"),
        ("/houndarr//login", "/login"),
        ("///api/health", "/api/health"),
        ("/houndarrx/login", "/houndarrx/login"),
        ("/houndarr/houndarr/login", "/houndarr/login"),
        ("/houndarr/../settings", "/../settings"),
        ("/houndarr/login\n", "/login\n"),
    ],
)
def test_auth_and_router_see_the_same_path(path: str, routed: str) -> None:
    scope = _rebase(_scope(path), _BASE)
    assert route_path(Request(scope)) == get_route_path(scope) == routed


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        (b"/houndarr/login", b"/houndarr/login"),
        (b"/login", b"/houndarr/login"),
        (b"//login", b"/houndarr/login"),
        (b"/houndarr", b"/houndarr/"),
        (b"/houndarr/a%2Fb", b"/houndarr/a%2Fb"),
    ],
)
def test_raw_path_stays_in_step_with_path(raw_path: bytes, expected: bytes) -> None:
    scope = _rebase(_scope(unquote(raw_path.decode()), raw_path), _BASE)
    assert scope["raw_path"] == expected


def test_raw_path_dropped_when_the_base_is_percent_encoded() -> None:
    scope = _rebase(_scope("/houndarr/login", b"/%68oundarr/login"), _BASE)
    assert scope["path"] == "/houndarr/login"
    assert "raw_path" not in scope


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (b"http://testserver/houndarr/settings", b"/houndarr/settings"),
        (b"https://testserver/settings?a=1#top", b"/settings?a=1#top"),
        (b"http://TESTSERVER/logs", b"/logs"),
        (b"/houndarr/login", b"/houndarr/login"),
        (b"http://other.example/settings", b"http://other.example/settings"),
        (b"http://testserver//evil.example/", b"http://testserver//evil.example/"),
        (b"http://testserver:8877/settings", b"http://testserver:8877/settings"),
    ],
)
def test_only_same_origin_locations_become_path_only(location: bytes, expected: bytes) -> None:
    assert _path_only(_scope("/"), location) == expected


# ---------------------------------------------------------------------------
# Builtin auth under a base
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_app(tmp_data_dir: str) -> Iterator[TestClient]:
    """TestClient for an app served under ``/houndarr``."""
    from houndarr.app import create_app
    from houndarr.auth import reset_auth_caches

    bootstrap_settings(data_dir=tmp_data_dir, url_base=_BASE)
    reset_auth_caches()
    try:
        with TestClient(create_app(), raise_server_exceptions=True) as client:
            yield client
    finally:
        bootstrap_settings()


@pytest.fixture()
def _stub_factory_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    import houndarr.routes.admin as admin_routes

    async def _noop_reset(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(admin_routes, "factory_reset", _noop_reset)


def _setup(client: TestClient, prefix: str = _BASE) -> None:
    client.post(f"{prefix}/setup", data=_SETUP_FORM)


def _setup_and_login(client: TestClient, prefix: str = _BASE) -> None:
    _setup(client, prefix)
    client.post(f"{prefix}/login", data={"username": "admin", "password": _PASSWORD})


def _url(path: str) -> str:
    """Return an absolute URL so httpx keeps a leading ``//`` in the path."""
    return f"http://testserver{path}"


def _cookie_names(set_cookies: list[str]) -> set[str]:
    return {c.split("=", 1)[0] for c in set_cookies}


def _cookie_paths(set_cookies: list[str]) -> set[str]:
    return {m.group(1) for c in set_cookies if (m := re.search(r"[Pp]ath=([^;]*)", c))}


def test_unauthenticated_redirect_keeps_base_path(base_app: TestClient) -> None:
    resp = base_app.get(f"{_BASE}/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_BASE}/setup"

    _setup_and_login(base_app)
    base_app.cookies.clear()
    for path in (f"{_BASE}/settings", "/settings"):
        resp = base_app.get(path, follow_redirects=False)
        assert resp.headers["location"] == f"{_BASE}/login"


def test_setup_login_logout_redirects_keep_base_path(base_app: TestClient) -> None:
    resp = base_app.post(f"{_BASE}/setup", data=_SETUP_FORM, follow_redirects=False)
    assert resp.headers["location"] == f"{_BASE}/login"

    resp = base_app.post(
        f"{_BASE}/login",
        data={"username": "admin", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{_BASE}/"
    set_cookies = resp.headers.get_list("set-cookie")
    assert _cookie_names(set_cookies) == {"houndarr_session", "houndarr_csrf"}
    assert _cookie_paths(set_cookies) == {"/"}

    resp = base_app.post(f"{_BASE}/logout", follow_redirects=False)
    assert resp.headers["location"] == f"{_BASE}/login"
    cleared = resp.headers.get_list("set-cookie")
    assert _cookie_names(cleared) == {"houndarr_session", "houndarr_csrf"}
    assert _cookie_paths(cleared) == {"/"}


@pytest.mark.parametrize(
    "path",
    [
        f"{_BASE}/login",
        "/login",
        "//login",
        f"{_BASE}//login",
        "/%68oundarr/login",
        f"{_BASE}/api/health",
        "/api/health",
        f"{_BASE}/static/js/app.js",
        "/static/js/app.js",
    ],
)
def test_public_paths_answer_anonymously_with_or_without_prefix(
    base_app: TestClient, path: str
) -> None:
    _setup(base_app)
    assert base_app.get(_url(path), follow_redirects=False).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        _BASE,
        f"{_BASE}/",
        "/",
        f"{_BASE}/settings",
        "/settings",
        "//settings",
        f"{_BASE}//settings",
        "/%68oundarr/settings",
        f"{_BASE}%2Fsettings",
        f"{_BASE}/api/status",
        "/api/status",
        f"{_BASE}/loginx",
        f"{_BASE}/staticx",
        f"{_BASE}/api/health/x",
        f"{_BASE}x/login",
        f"{_BASE}/{_BASE}/login",
        f"{_BASE}/%2e%2e/settings",
        f"{_BASE}/settings%0a",
    ],
)
def test_protected_and_unknown_paths_require_login(base_app: TestClient, path: str) -> None:
    _setup(base_app)
    resp = base_app.get(_url(path), follow_redirects=False)
    assert resp.status_code == 302, resp.text[:200]
    assert resp.headers["location"] == f"{_BASE}/login"


def test_signed_in_pages_answer_with_or_without_prefix(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    for path in (_BASE, f"{_BASE}/", "/", f"{_BASE}/logs", "/logs", "//settings"):
        resp = base_app.get(_url(path), follow_redirects=False)
        assert resp.status_code == 200, path
        assert 'id="app-content"' in resp.text


def test_trailing_slash_redirect_is_path_only(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    for path in (f"{_BASE}/settings/", "/settings/"):
        resp = base_app.get(path, follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == f"{_BASE}/settings"


def test_trailing_slash_redirect_is_path_only_at_the_root(app: TestClient) -> None:
    _setup_and_login(app, prefix="")
    resp = app.get("/settings/?tab=admin", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/settings?tab=admin"


def test_static_assets_keep_cache_headers_under_base(base_app: TestClient) -> None:
    for path in (f"{_BASE}/static/js/app.js", "/static/js/app.js"):
        resp = base_app.get(path)
        assert resp.status_code == 200
        assert "immutable" in resp.headers["cache-control"]


async def _store_widget_key() -> str:
    token = generate_api_key()
    await widget_api_key.set(hash_api_key(token))
    return token


@pytest.mark.parametrize("path", [f"{_BASE}/api/v1/widget", "/api/v1/widget"])
def test_widget_key_lane_answers_with_or_without_prefix(base_app: TestClient, path: str) -> None:
    _setup(base_app)
    assert base_app.get(path).status_code == 401
    token = asyncio.run(_store_widget_key())
    resp = base_app.get(path, headers={"X-Api-Key": token})
    assert resp.status_code == 200
    assert resp.json()["schema"] == 1


@pytest.mark.usefixtures("_stub_factory_reset")
def test_factory_reset_redirect_keeps_base_path(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    resp = base_app.post(
        f"{_BASE}/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "current_password": _PASSWORD},
        headers=csrf_headers(base_app),
    )
    assert resp.headers["HX-Redirect"] == f"{_BASE}/setup"
    assert _cookie_paths(resp.headers.get_list("set-cookie")) == {"/"}


def test_password_change_flash_cookie_stays_at_root_path(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    resp = base_app.post(
        f"{_BASE}/settings/account/password",
        data={
            "current_password": _PASSWORD,
            "new_password": "NewValidPass2!",
            "new_password_confirm": "NewValidPass2!",
        },
        headers=csrf_headers(base_app),
    )
    assert resp.status_code == 200
    flash = [c for c in resp.headers.get_list("set-cookie") if c.startswith("houndarr_flash=")]
    assert len(flash) == 1
    assert _cookie_paths(flash) == {"/"}


def test_reset_instances_oob_rows_carry_base_path(
    base_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _reachable(self: ArrClient) -> SystemStatus | None:
        return SystemStatus(app_name="Sonarr", version="4.0.0")

    monkeypatch.setattr(ArrClient, "ping", _reachable)
    _setup_and_login(base_app)
    base_app.post(
        f"{_BASE}/settings/instances", data=_INSTANCE_FORM, headers=csrf_headers(base_app)
    )
    resp = base_app.post(f"{_BASE}/settings/admin/reset-instances", headers=csrf_headers(base_app))
    assert resp.status_code == 200
    assert 'hx-swap-oob="innerHTML:#instance-tbody"' in resp.text
    assert f'"{_BASE}/settings/instances/1/edit"' in resp.text
    assert not re.search(r'(?:href|src|action|hx-get|hx-post|hx-delete)="/(?!houndarr/)', resp.text)


def test_nav_active_state_ignores_base_path(base_app: TestClient) -> None:
    _setup_and_login(base_app)
    for path in (f"{_BASE}/logs", "/logs"):
        html = base_app.get(path).text
        logs_link = re.search(r'<a [^>]*data-shell-route="/logs"[^>]*>', html)
        dash_link = re.search(r'<a [^>]*data-shell-route="/"[^>]*>', html)
        assert logs_link and "pill-nav__tab--active" in logs_link.group(0)
        assert dash_link and "pill-nav__tab--active" not in dash_link.group(0)


def test_default_app_has_no_prefix(app: TestClient) -> None:
    resp = app.get("/", follow_redirects=False)
    assert resp.headers["location"] == "/setup"
    html = app.get("/setup").text
    assert 'href="/static/css/app.built.css' in html
    assert 'basePath: ""' in html


# ---------------------------------------------------------------------------
# Proxy auth under a base
# ---------------------------------------------------------------------------


def _proxy_client(app: FastAPI, client_ip: str) -> TestClient:
    async def _from(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope["client"] = (client_ip, 0)
        await app(scope, receive, send)

    return TestClient(_from, raise_server_exceptions=True)


@pytest.fixture()
def proxy_base_app(tmp_data_dir: str) -> Iterator[TestClient]:
    """Proxy-auth TestClient under ``/houndarr``, calling from the trusted proxy."""
    from houndarr.app import create_app
    from houndarr.auth import reset_auth_caches

    bootstrap_settings(
        data_dir=tmp_data_dir,
        url_base=_BASE,
        auth_mode="proxy",
        auth_proxy_header=_AUTH_HEADER,
        trusted_proxies=_TRUSTED_IP,
    )
    reset_auth_caches()
    try:
        with _proxy_client(create_app(), _TRUSTED_IP) as client:
            yield client
    finally:
        bootstrap_settings()


@pytest.mark.parametrize("path", [f"{_BASE}/login", "/login", f"{_BASE}/setup", "/setup"])
def test_proxy_dead_paths_redirect_under_base(proxy_base_app: TestClient, path: str) -> None:
    resp = proxy_base_app.get(path, headers={_AUTH_HEADER: "alice"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_BASE}/"


def test_proxy_logout_redirects_under_base_and_clears_csrf(proxy_base_app: TestClient) -> None:
    resp = proxy_base_app.post(
        f"{_BASE}/logout", headers={_AUTH_HEADER: "alice"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_BASE}/"
    cleared = resp.headers.get_list("set-cookie")
    assert [c.split("=", 1)[0] for c in cleared] == ["houndarr_csrf"]
    assert _cookie_paths(cleared) == {"/"}


@pytest.mark.parametrize("path", [f"{_BASE}/settings", "/settings"])
def test_proxy_pages_need_the_auth_header_under_base(proxy_base_app: TestClient, path: str) -> None:
    assert proxy_base_app.get(path, follow_redirects=False).status_code == 401
    resp = proxy_base_app.get(path, headers={_AUTH_HEADER: "alice"}, follow_redirects=False)
    assert resp.status_code == 200
    assert f'hx-get="{_BASE}/settings/instances/add-form"' in resp.text
    csrf = [c for c in resp.headers.get_list("set-cookie") if c.startswith("houndarr_csrf=")]
    assert _cookie_paths(csrf) == {"/"}


@pytest.mark.parametrize("path", [f"{_BASE}/api/health", "/api/health"])
def test_proxy_health_is_public_under_base(proxy_base_app: TestClient, path: str) -> None:
    assert proxy_base_app.get(path).status_code == 200


@pytest.mark.usefixtures("_stub_factory_reset")
def test_proxy_factory_reset_redirects_to_base(proxy_base_app: TestClient) -> None:
    proxy_base_app.get(f"{_BASE}/settings", headers={_AUTH_HEADER: "alice"})
    resp = proxy_base_app.post(
        f"{_BASE}/settings/admin/factory-reset",
        data={"confirm_phrase": "RESET", "confirm_username": "alice"},
        headers={_AUTH_HEADER: "alice", **csrf_headers(proxy_base_app)},
    )
    assert resp.headers["HX-Redirect"] == f"{_BASE}/"

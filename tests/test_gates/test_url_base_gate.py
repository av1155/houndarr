"""Every URL Houndarr sends to the browser carries ``HOUNDARR_URL_BASE`` (#918).

Houndarr answers prefixed and unprefixed paths alike, so a URL that skips
the base still works on a direct connection and breaks only behind a proxy
that serves nothing outside the prefix.  The source scans catch such a URL
in templates, static JS and redirects; the rendered check runs the pages
under a base and reads what the browser would get.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from houndarr.auth.houndarr_api_key import generate_api_key, hash_api_key
from houndarr.clients._wire_models import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.config import bootstrap_settings
from houndarr.database import get_db
from houndarr.repositories import widget_api_key
from tests.conftest import csrf_headers

_SRC = Path(__file__).resolve().parents[2] / "src" / "houndarr"
_TEMPLATES = _SRC / "templates"
_JS = _SRC / "static" / "js"
_VENDORED_JS = frozenset({"htmx.min.js"})

_URL_ATTRS = (
    "href|src|action|formaction|poster|hx-get|hx-post|hx-put|hx-patch|hx-delete"
    "|hx-push-url|hx-replace-url"
)
_ATTR_RE = re.compile(rf"(?<![\w-])({_URL_ATTRS})\s*=\s*(['\"])(.*?)\2", re.DOTALL)
_DICT_ATTR_RE = re.compile(rf"(['\"])({_URL_ATTRS})\1\s*:\s*(['\"])/")
_EXPR_HEAD_RE = re.compile(r"\{\{-?\s*([\w.]+)")
_COMMENT_RE = re.compile(r"\{#.*?#\}|<!--.*?-->", re.DOTALL)
_IMPORT_RE = re.compile(r"\{%-?\s*(?:import|from)\s+(['\"])(.+?)\1(.*?)-?%\}", re.DOTALL)
_JS_PATH_RE = re.compile(r"(['\"`])(/[A-Za-z][\w.~-]*(?:/[\w.~${}-]*)*(?:\?[^'\"`\s]*)?)\1")

# Expressions that hold absolute links to GitHub, never an app route.
_EXTERNAL_URL_EXPRESSIONS = frozenset({"s.release_url"})
_URL_HEADERS = frozenset({"location", "hx-redirect", "hx-location", "hx-push-url"})


def template_violations(text: str) -> list[str]:
    """Return URL attributes in a Jinja template that skip ``base_path``."""
    text = _COMMENT_RE.sub("", text)
    found = []
    for match in _ATTR_RE.finditer(text):
        value = match.group(3).strip()
        if value.startswith("{{"):
            head = _EXPR_HEAD_RE.match(value)
            if head and head.group(1) in {"base_path", *_EXTERNAL_URL_EXPRESSIONS}:
                continue
            found.append(match.group(0))
        elif value.startswith(("/", "{%")):
            found.append(match.group(0))
    found.extend(match.group(0) for match in _DICT_ATTR_RE.finditer(text))
    return found


def import_violations(text: str, uses_base_path: set[str]) -> list[str]:
    """Return imports of a ``base_path`` template that drop the caller's context."""
    return [
        match.group(0)
        for match in _IMPORT_RE.finditer(_COMMENT_RE.sub("", text))
        if match.group(2) in uses_base_path and "with context" not in match.group(3)
    ]


def js_violations(text: str) -> list[str]:
    """Return root-absolute path literals in JS; app URLs start with ``${BASE_PATH}``."""
    return [match.group(0) for match in _JS_PATH_RE.finditer(text)]


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _argument(node: ast.Call, keyword: str, position: int) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == keyword:
            return kw.value
    return node.args[position] if len(node.args) > position else None


def _is_literal(node: ast.expr | None) -> bool:
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def redirect_violations(source: str) -> list[str]:
    """Return redirects built from a path literal instead of ``app_url``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if (
                (name == "url_for")
                or (name == "RedirectResponse" and _is_literal(_argument(node, "url", 0)))
                or (name == "hx_redirect_response" and _is_literal(_argument(node, "location", 1)))
            ):
                found.append(ast.unparse(node))
        elif isinstance(node, ast.Assign) and _is_literal(node.value):
            for target in node.targets:
                key = target.slice if isinstance(target, ast.Subscript) else None
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value.lower() in _URL_HEADERS
                ):
                    found.append(ast.unparse(node))
    return found


def _templates() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


def _js_files() -> list[Path]:
    return sorted(p for p in _JS.glob("*.js") if p.name not in _VENDORED_JS)


@pytest.mark.parametrize("path", _templates(), ids=lambda p: str(p.relative_to(_TEMPLATES)))
def test_template_urls_carry_the_base(path: Path) -> None:
    assert template_violations(path.read_text(encoding="utf-8")) == []


def test_templates_using_base_path_are_imported_with_context() -> None:
    texts = {str(p.relative_to(_TEMPLATES)): p.read_text(encoding="utf-8") for p in _templates()}
    uses_base_path = {name for name, text in texts.items() if "base_path" in text}
    found = {
        name: violations
        for name, text in texts.items()
        if (violations := import_violations(text, uses_base_path))
    }
    assert found == {}


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_js_urls_carry_the_base(path: Path) -> None:
    assert js_violations(path.read_text(encoding="utf-8")) == []


def test_redirects_go_through_app_url() -> None:
    found = {
        str(p.relative_to(_SRC)): violations
        for p in sorted(_SRC.rglob("*.py"))
        if (violations := redirect_violations(p.read_text(encoding="utf-8")))
    }
    assert found == {}


@pytest.mark.parametrize(
    ("snippet", "flagged"),
    [
        ('<a href="/logs">', True),
        ("<form hx-post='/settings/instances'>", True),
        ('<div hx-push-url="/logs">', True),
        ('<a href="{{ url }}">', True),
        ('<a href="{% if x %}/a{% endif %}">', True),
        ('{"hx-get": "/api/status"}', True),
        ('<a href="{{ base_path }}/logs">', False),
        ('<a href="{{- base_path ~ "/x" }}">', False),
        ('<a data-shell-route="/logs">', False),
        ('<a data-hx-get="/x">', False),
        ('<a href="https://github.com/av1155/houndarr">', False),
        ('<a href="#top" hx-push-url="true">', False),
        ('{# <a href="/old"> #}', False),
    ],
)
def test_template_rule_flags_planted_urls(snippet: str, flagged: bool) -> None:
    assert bool(template_violations(snippet)) is flagged


@pytest.mark.parametrize(
    ("snippet", "flagged"),
    [
        ("fetch('/api/status')", True),
        ('el.setAttribute("hx-get", "/logs?x=1")', True),
        ("const u = `/settings/instances/${id}`;", True),
        ("fetch(`${BASE_PATH}/api/status`)", False),
        ("text.replace(/\"/g, '&quot;').replace(/'/g, '&#39;')", False),
        ("if (route === '/') return true;", False),
    ],
)
def test_js_rule_flags_planted_urls(snippet: str, flagged: bool) -> None:
    assert bool(js_violations(snippet)) is flagged


@pytest.mark.parametrize(
    ("snippet", "flagged"),
    [
        ('{% import "_macros/htmx.html" as hx %}', True),
        ('{%- from "_macros/htmx.html" import shell_nav_link -%}', True),
        ('{% import "_macros/htmx.html" as hx with context %}', False),
        ('{% import "_macros/buttons.html" as buttons %}', False),
    ],
)
def test_import_rule_flags_planted_imports(snippet: str, flagged: bool) -> None:
    assert bool(import_violations(snippet, {"_macros/htmx.html"})) is flagged


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ('RedirectResponse(url="/login", status_code=302)', True),
        ('RedirectResponse(f"/x/{y}")', True),
        ('hx_redirect_response(response, "/setup")', True),
        ('request.url_for("login")', True),
        ('response.headers["HX-Redirect"] = "/setup"', True),
        ('RedirectResponse(url=app_url(request, "/login"), status_code=302)', False),
        ("hx_redirect_response(response, target)", False),
        ('response.headers["HX-Trigger"] = "refresh"', False),
    ],
)
def test_redirect_rule_flags_planted_redirects(source: str, flagged: bool) -> None:
    assert bool(redirect_violations(source)) is flagged


# ---------------------------------------------------------------------------
# Rendered pages under a base
# ---------------------------------------------------------------------------

_GATE_BASE = "/hx-gate"
_PASSWORD = "ValidPass1!"


def rendered_violations(html: str, base: str) -> list[str]:
    """Return rendered URL attributes that miss *base* or repeat it."""
    found = []
    for match in _ATTR_RE.finditer(html):
        value = match.group(3).strip()
        if not value.startswith("/"):
            continue
        if not value.startswith(f"{base}/") or value.startswith(f"{base}{base}"):
            found.append(match.group(0))
    return found


@pytest.mark.parametrize(
    ("html", "flagged"),
    [
        ('<a href="/logs">', True),
        ('<a hx-get="/hx-gate/hx-gate/logs">', True),
        ('<a href="/hx-gatex/logs">', True),
        ('<a href="/hx-gate/">', False),
        ('<a href="/hx-gate/logs?x=1">', False),
        ('<a href="https://github.com/av1155/houndarr">', False),
    ],
)
def test_rendered_rule_flags_planted_urls(html: str, flagged: bool) -> None:
    assert bool(rendered_violations(html, _GATE_BASE)) is flagged


async def _seed_log_row() -> None:
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, action, message, timestamp) "
            "VALUES (1, 'error', 'Could not reach http://sonarr:8989', datetime('now'))"
        )
        await conn.commit()


async def _store_widget_key() -> None:
    await widget_api_key.set(hash_api_key(generate_api_key()))


@pytest.fixture()
def gate_app(tmp_data_dir: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from houndarr.app import create_app
    from houndarr.auth import reset_auth_caches

    async def _reachable(self: ArrClient) -> SystemStatus | None:
        return SystemStatus(app_name="Sonarr", version="4.0.0")

    monkeypatch.setattr(ArrClient, "ping", _reachable)
    bootstrap_settings(data_dir=tmp_data_dir, url_base=_GATE_BASE)
    reset_auth_caches()
    try:
        with TestClient(create_app(), raise_server_exceptions=True) as client:
            yield client
    finally:
        bootstrap_settings()


def test_rendered_pages_carry_the_base_once(gate_app: TestClient) -> None:
    base = _GATE_BASE
    pages: dict[str, str] = {"setup": gate_app.get(f"{base}/setup").text}
    gate_app.post(
        f"{base}/setup",
        data={"username": "admin", "password": _PASSWORD, "password_confirm": _PASSWORD},
    )
    pages["login"] = gate_app.get(f"{base}/login").text
    gate_app.post(f"{base}/login", data={"username": "admin", "password": _PASSWORD})
    headers = csrf_headers(gate_app)
    pages["instance-create"] = gate_app.post(
        f"{base}/settings/instances",
        data={
            "name": "My Sonarr",
            "type": "sonarr",
            "url": "http://sonarr:8989",
            "api_key": "test-api-key",
            "sonarr_search_mode": "episode",
            "connection_verified": "true",
        },
        headers=headers,
    ).text
    asyncio.run(_seed_log_row())
    for name, path in {
        "dashboard": "/",
        "logs": "/logs",
        "log-rows": "/api/logs/partial",
        "settings": "/settings",
        "help": "/settings/help",
        "add-form": "/settings/instances/add-form",
        "edit-form": "/settings/instances/1/edit",
        "changelog": "/settings/changelog/popup?manual=true",
    }.items():
        resp = gate_app.get(f"{base}{path}")
        assert resp.status_code == 200, name
        pages[name] = resp.text
    for name, path in {
        "toggle": "/settings/instances/1/toggle-enabled",
        "api-key-generate": "/settings/api-key/generate",
        "api-key-revoke": "/settings/api-key/revoke",
        "reset-instances": "/settings/admin/reset-instances",
    }.items():
        resp = gate_app.post(f"{base}{path}", headers=headers)
        assert resp.status_code == 200, name
        pages[name] = resp.text
    asyncio.run(_store_widget_key())
    pages["settings-with-key"] = gate_app.get(f"{base}/settings").text

    assert f'basePath: "{base}"' in pages["dashboard"]
    assert f'href="{base}/static/css/app.built.css' in pages["login"]
    found = {name: v for name, html in pages.items() if (v := rendered_violations(html, base))}
    assert found == {}

"""Tests for the global ``CacheControlMiddleware``.

The middleware sets three different cache contracts based on response
shape: ``no-cache`` for HTML (with ``Vary: HX-Request`` so HTMX
partials and full pages cache separately), an aggressive
``public, max-age=31536000, immutable`` for ``/static/*`` 2xx
responses (safe because every URL is version-busted via
``?v={{ version }}``), and ``no-store`` for JSON polling endpoints.
Each contract has its own test so a regression on one rule does not
mask the others.

The first block exercises the contract through the real Houndarr
app (auth flow, real routes, real templates) so the middleware is
verified against the same response shapes it actually sees in
production.  The second block uses a synthetic FastAPI app wired
only with ``CacheControlMiddleware`` so edge cases (route-level
``Cache-Control`` preservation, pre-existing ``Vary``, non-2xx
static) can be asserted without dragging in the auth and template
machinery.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.testclient import TestClient

from houndarr.cache_headers import CacheControlMiddleware

# ---------------------------------------------------------------------------
# Real-app coverage: full integration through create_app() + TestClient
# ---------------------------------------------------------------------------


def test_html_setup_page_sets_no_cache(app: TestClient) -> None:
    """A full GET on a public HTML page returns ``Cache-Control: no-cache``."""
    resp = app.get("/setup", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "no-cache"


def test_html_setup_page_sets_vary_hx_request(app: TestClient) -> None:
    """HTML responses declare ``Vary: HX-Request`` so HTMX partials
    and full pages stay in separate browser-cache entries."""
    resp = app.get("/setup", follow_redirects=False)
    assert resp.status_code == 200
    vary_parts = [p.strip() for p in resp.headers.get("vary", "").split(",")]
    assert "HX-Request" in vary_parts


def test_htmx_partial_response_sets_no_cache_and_vary(app: TestClient) -> None:
    """HTMX-flagged GET that produces an HTML partial gets the same
    cache contract as the full-page render: revalidate-on-load with a
    Vary so the partial does not leak into the full-page cache slot."""
    resp = app.get("/setup", headers={"HX-Request": "true"}, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "no-cache"
    vary_parts = [p.strip() for p in resp.headers.get("vary", "").split(",")]
    assert "HX-Request" in vary_parts


def test_auth_redirect_html_body_sets_no_cache(app: TestClient) -> None:
    """Auth middleware redirects unauthenticated requests via 302 with
    a small HTML body; those responses must also revalidate so a stale
    cached redirect does not strand a logged-in browser on /login."""
    resp = app.get("/", follow_redirects=False)
    assert resp.status_code in {302, 307}
    if resp.headers.get("content-type", "").startswith("text/html"):
        assert resp.headers["cache-control"] == "no-cache"


def test_static_2xx_sets_immutable(app: TestClient) -> None:
    """Static assets are version-busted via ``?v={{ version }}``, so
    a 200 response can be cached for a year with ``immutable``."""
    resp = app.get("/static/js/app.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_2xx_query_string_does_not_change_header(app: TestClient) -> None:
    """The version query string is part of the URL but the
    Cache-Control contract is identical regardless of its value."""
    resp = app.get("/static/js/app.js?v=1.10.0")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_css_sets_immutable(app: TestClient) -> None:
    """CSS assets get the same immutable contract as JS.

    Targets ``app.css`` (a tracked source file) rather than
    ``app.built.css``: the latter is compiled at Docker build time
    by the css-build stage and is gitignored, so it does not exist
    when tests run against the checked-out source tree in CI.
    """
    resp = app.get("/static/css/app.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_image_sets_immutable(app: TestClient) -> None:
    """Image assets get the same immutable contract as CSS/JS.

    Logos and favicons in ``/static/img/`` are referenced from
    ``base.html`` with the same ``?v={{ version }}`` cache buster as
    the bundled CSS/JS.
    """
    resp = app.get("/static/img/houndarr-logo-dark.png")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_404_does_not_get_immutable_cache(app: TestClient) -> None:
    """``immutable`` on a 404 would lock browsers into a missing-asset
    state for a year.  The middleware skips the static rule on
    non-2xx and falls through to the content-type rules; FastAPI's
    default 404 body is JSON, so the response gets ``no-store``."""
    resp = app.get("/static/this-file-does-not-exist.css")
    assert resp.status_code == 404
    assert resp.headers.get("cache-control") != "public, max-age=31536000, immutable"
    if resp.headers.get("content-type", "").startswith("application/json"):
        assert resp.headers["cache-control"] == "no-store"


def test_api_health_json_sets_no_store(app: TestClient) -> None:
    """Polling JSON endpoints must never be served from a cache."""
    resp = app.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["cache-control"] == "no-store"


def test_api_health_json_does_not_get_html_vary(app: TestClient) -> None:
    """Non-HTML responses skip the ``Vary: HX-Request`` rule because
    they never branch on the HX-Request header."""
    resp = app.get("/api/health")
    vary = resp.headers.get("vary", "")
    assert "HX-Request" not in [p.strip() for p in vary.split(",")]


def test_repeated_html_request_keeps_single_vary_entry(app: TestClient) -> None:
    """Requesting the same HTML page twice must not duplicate
    ``HX-Request`` in the Vary header.  TestClient creates a fresh
    response per request, so this guards against a future change that
    re-runs the middleware against an already-stamped response."""
    first = app.get("/setup", follow_redirects=False)
    second = app.get("/setup", follow_redirects=False)
    for resp in (first, second):
        vary_parts = [p.strip() for p in resp.headers.get("vary", "").split(",")]
        assert vary_parts.count("HX-Request") == 1


def test_authenticated_dashboard_html_sets_no_cache(app: TestClient) -> None:
    """A real authenticated HTML page (the dashboard) carries the same
    cache contract as the public setup page.  Verifies the middleware
    runs on responses produced after auth has passed."""
    app.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    app.post("/login", data={"username": "admin", "password": "ValidPass1!"})
    resp = app.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "no-cache"
    assert "HX-Request" in [p.strip() for p in resp.headers.get("vary", "").split(",")]


# ---------------------------------------------------------------------------
# Synthetic-app coverage: edge cases on response variants the real app
# does not currently emit but the middleware must still handle correctly
# ---------------------------------------------------------------------------


@pytest.fixture()
def synthetic_app() -> Generator[TestClient, None, None]:
    """Build a minimal FastAPI app with only ``CacheControlMiddleware``."""
    fastapi_app = FastAPI()
    fastapi_app.add_middleware(CacheControlMiddleware)

    @fastapi_app.get("/html-default")
    async def html_default() -> HTMLResponse:
        return HTMLResponse("<p>hi</p>")

    @fastapi_app.get("/html-with-cache")
    async def html_with_cache() -> HTMLResponse:
        # Route opts out of the middleware default by setting its own
        # Cache-Control.  setdefault inside the middleware must not
        # overwrite this.
        return HTMLResponse(
            "<p>hi</p>",
            headers={"Cache-Control": "private, max-age=60"},
        )

    @fastapi_app.get("/html-with-vary")
    async def html_with_vary() -> HTMLResponse:
        # Route already declares a Vary dimension; middleware must
        # append HX-Request rather than overwrite.
        return HTMLResponse("<p>hi</p>", headers={"Vary": "Accept-Language"})

    @fastapi_app.get("/json-default")
    async def json_default() -> JSONResponse:
        return JSONResponse({"ok": True})

    @fastapi_app.get("/json-with-cache")
    async def json_with_cache() -> JSONResponse:
        return JSONResponse(
            {"ok": True},
            headers={"Cache-Control": "public, max-age=30"},
        )

    @fastapi_app.get("/redirect")
    async def redirect_response() -> RedirectResponse:
        return RedirectResponse(url="/somewhere", status_code=302)

    @fastapi_app.get("/plain")
    async def plain_text() -> PlainTextResponse:
        return PlainTextResponse("hello")

    @fastapi_app.get("/empty")
    async def empty_response() -> Response:
        return Response(status_code=204)

    @fastapi_app.get("/html-422")
    async def html_422() -> HTMLResponse:
        # Mirrors the real exception handler in app.py for HTMX
        # validation errors.
        return HTMLResponse(content="", status_code=422, headers={"HX-Reswap": "none"})

    @fastapi_app.post("/json-201")
    async def json_201(payload: dict[str, Any]) -> JSONResponse:
        return JSONResponse(payload, status_code=201)

    @fastapi_app.get("/static/known.css")
    async def static_known() -> Response:
        return Response(content="body{}", media_type="text/css")

    @fastapi_app.get("/static/route-set.css")
    async def static_route_set() -> Response:
        # A static-prefixed path that explicitly sets Cache-Control
        # must keep its setting.
        return Response(
            content="body{}",
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @fastapi_app.get("/static/error.css")
    async def static_error() -> Response:
        # Non-2xx response on /static/ must NOT get the immutable
        # header (otherwise browsers cache the failure for a year).
        return Response(content="boom", status_code=500, media_type="text/plain")

    @fastapi_app.get("/static/error.json")
    async def static_error_json() -> JSONResponse:
        # JSON-bodied 404 on /static/ should fall through to the JSON
        # rule so the error response still gets a Cache-Control.
        return JSONResponse({"detail": "not found"}, status_code=404)

    @fastapi_app.get("/utf8-html")
    async def utf8_html() -> HTMLResponse:
        # Real templates emit "text/html; charset=utf-8".  The
        # content-type prefix check must still match.
        return HTMLResponse("<p>hi</p>", headers={"Content-Type": "text/html; charset=utf-8"})

    @fastapi_app.get("/utf8-json")
    async def utf8_json() -> Response:
        return Response(
            content='{"ok":true}',
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    with TestClient(fastapi_app, raise_server_exceptions=True) as client:
        yield client


def test_html_default_gets_no_cache(synthetic_app: TestClient) -> None:
    resp = synthetic_app.get("/html-default")
    assert resp.headers["cache-control"] == "no-cache"
    assert "HX-Request" in [p.strip() for p in resp.headers["vary"].split(",")]


def test_html_route_cache_control_preserved(synthetic_app: TestClient) -> None:
    """A route that explicitly sets ``Cache-Control`` keeps its value;
    the middleware uses ``setdefault`` so the route is authoritative.
    Vary is still appended so HTMX caching stays correct."""
    resp = synthetic_app.get("/html-with-cache")
    assert resp.headers["cache-control"] == "private, max-age=60"
    assert "HX-Request" in [p.strip() for p in resp.headers["vary"].split(",")]


def test_html_route_vary_preserved_and_extended(synthetic_app: TestClient) -> None:
    """A route that already declares ``Vary`` keeps its dimension and
    gains ``HX-Request`` alongside.  Order: the route's value first,
    then ``HX-Request``."""
    resp = synthetic_app.get("/html-with-vary")
    parts = [p.strip() for p in resp.headers["vary"].split(",")]
    assert "Accept-Language" in parts
    assert "HX-Request" in parts


def test_json_default_gets_no_store(synthetic_app: TestClient) -> None:
    resp = synthetic_app.get("/json-default")
    assert resp.headers["cache-control"] == "no-store"


def test_json_route_cache_control_preserved(synthetic_app: TestClient) -> None:
    """JSON route that opts into caching keeps its own header."""
    resp = synthetic_app.get("/json-with-cache")
    assert resp.headers["cache-control"] == "public, max-age=30"


def test_json_201_gets_no_store(synthetic_app: TestClient) -> None:
    """JSON contract is content-type driven, not status-code driven:
    a 201 Created with JSON body still gets ``no-store``."""
    resp = synthetic_app.post("/json-201", json={"hello": "world"})
    assert resp.status_code == 201
    assert resp.headers["cache-control"] == "no-store"


def test_redirect_response_handled_safely(synthetic_app: TestClient) -> None:
    """RedirectResponse has no body and no relevant content-type for
    our rules.  The middleware must not crash and must not set a
    Cache-Control that would prevent the browser from following the
    redirect freshly."""
    resp = synthetic_app.get("/redirect", follow_redirects=False)
    assert resp.status_code == 302
    # Either the redirect carries no Cache-Control, or it carries
    # no-cache (if Starlette set a text/html content-type).  Both are
    # safe; the cache must never be ``immutable`` or a long max-age.
    cache = resp.headers.get("cache-control", "")
    assert "immutable" not in cache
    assert "max-age=31536000" not in cache


def test_plain_text_response_unaffected(synthetic_app: TestClient) -> None:
    """``text/plain`` responses fall through every rule.  The middleware
    must not invent a Cache-Control header for content types it does
    not know about."""
    resp = synthetic_app.get("/plain")
    assert resp.status_code == 200
    assert "cache-control" not in {k.lower() for k in resp.headers}


def test_empty_response_unaffected(synthetic_app: TestClient) -> None:
    """A 204 No Content response with no body and no content-type must
    not gain a Cache-Control header from any rule."""
    resp = synthetic_app.get("/empty")
    assert resp.status_code == 204
    assert "cache-control" not in {k.lower() for k in resp.headers}


def test_html_422_validation_error_gets_no_cache(synthetic_app: TestClient) -> None:
    """Mirrors the real ``RequestValidationError`` HTMX response shape
    in ``app.py``: empty HTML body with ``HX-Reswap: none`` at status
    422.  The cache rule still fires because content-type is HTML,
    and ``HX-Reswap`` is preserved alongside the new headers."""
    resp = synthetic_app.get("/html-422")
    assert resp.status_code == 422
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers.get("HX-Reswap") == "none"
    assert "HX-Request" in [p.strip() for p in resp.headers["vary"].split(",")]


def test_static_known_2xx_gets_immutable(synthetic_app: TestClient) -> None:
    resp = synthetic_app.get("/static/known.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_route_cache_control_preserved(synthetic_app: TestClient) -> None:
    """A route serving ``/static/*`` that explicitly sets a different
    Cache-Control keeps its setting.  This guards future routes that
    serve static-prefixed content but legitimately want a different
    cache contract."""
    resp = synthetic_app.get("/static/route-set.css")
    assert resp.headers["cache-control"] == "no-store"


def test_static_5xx_does_not_get_immutable(synthetic_app: TestClient) -> None:
    """Server errors on /static/ must not be cached for a year."""
    resp = synthetic_app.get("/static/error.css")
    assert resp.status_code == 500
    assert resp.headers.get("cache-control") != "public, max-age=31536000, immutable"


def test_static_json_4xx_falls_through_to_no_store(synthetic_app: TestClient) -> None:
    """JSON-bodied error on /static/ falls through to the JSON rule
    instead of receiving the immutable static cache."""
    resp = synthetic_app.get("/static/error.json")
    assert resp.status_code == 404
    assert resp.headers["cache-control"] == "no-store"


def test_html_with_charset_param_matched(synthetic_app: TestClient) -> None:
    """``text/html; charset=utf-8`` (what Jinja2Templates actually
    emits) matches the prefix check."""
    resp = synthetic_app.get("/utf8-html")
    assert resp.headers["cache-control"] == "no-cache"
    assert "HX-Request" in [p.strip() for p in resp.headers["vary"].split(",")]


def test_json_with_charset_param_matched(synthetic_app: TestClient) -> None:
    """``application/json; charset=utf-8`` matches the prefix check."""
    resp = synthetic_app.get("/utf8-json")
    assert resp.headers["cache-control"] == "no-store"

"""Middleware that sets cache-related response headers.

Three rules apply per response, keyed off the request path and the
response ``Content-Type``:

1. HTML documents and HTMX partials default to ``Cache-Control:
   no-cache`` plus ``Vary: HX-Request``.  ``no-cache`` is MDN's
   canonical recommendation for HTML: the browser may store the
   response but must revalidate it on every load, so a deploy that
   changes the cache-busted asset URL
   (``/static/css/app.built.css?v={{ version }}``) is picked up on the
   next navigation instead of after a manual hard reload.  ``Vary:
   HX-Request`` is required because routes such as ``GET /dashboard``
   return either a full page or an ``#app-content`` partial depending
   on the request header (``is_hx_request``); without the ``Vary``
   directive, restoring a closed tab can render a stored partial as
   the entire page.

2. ``/static/*`` 2xx responses default to ``Cache-Control: public,
   max-age=31536000, immutable``.  ``base.html`` busts these via
   ``?v={{ version }}`` so the URL changes on every release; the
   ``immutable`` directive lets browsers skip revalidation entirely
   between releases.  Non-2xx status codes are left untouched so 404s
   are not locked into a year of caching.

3. JSON responses (``Content-Type: application/json``) default to
   ``Cache-Control: no-store``.  ``/api/status`` and ``/api/logs``
   are polled and must never be served from a cache.  Browsers
   usually do not cache these anyway; the explicit header is defence
   against intermediate proxies.

All three branches use ``setdefault`` for ``Cache-Control`` so a
route that has already set a ``Cache-Control`` header keeps its
value: the middleware only fills in a sensible default when the
route was silent.  The ``Vary`` branch appends ``HX-Request`` to any
pre-existing ``Vary`` value rather than overwriting it.
"""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

_HTML_CONTENT_TYPE_PREFIX = "text/html"
_JSON_CONTENT_TYPE_PREFIX = "application/json"
_STATIC_PATH_PREFIX = "/static/"
_VARY_HX_REQUEST = "HX-Request"

_HTML_CACHE_CONTROL = "no-cache"
_STATIC_CACHE_CONTROL = "public, max-age=31536000, immutable"
_JSON_CACHE_CONTROL = "no-store"


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Set ``Cache-Control`` and ``Vary`` headers based on response shape."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Apply the three cache rules to *response* before returning."""
        response = await call_next(request)

        path = request.url.path
        is_static_2xx = path.startswith(_STATIC_PATH_PREFIX) and 200 <= response.status_code < 300
        if is_static_2xx:
            response.headers.setdefault("Cache-Control", _STATIC_CACHE_CONTROL)
            return response

        # Non-2xx static responses (e.g. a 404 served as JSON by the
        # framework) fall through to the content-type rules so the
        # error body still picks up a sensible Cache-Control rather
        # than relying on browser heuristic freshness.
        content_type = response.headers.get("content-type", "").lower()
        if content_type.startswith(_HTML_CONTENT_TYPE_PREFIX):
            response.headers.setdefault("Cache-Control", _HTML_CACHE_CONTROL)
            _add_vary(response, _VARY_HX_REQUEST)
        elif content_type.startswith(_JSON_CONTENT_TYPE_PREFIX):
            response.headers.setdefault("Cache-Control", _JSON_CACHE_CONTROL)
        return response


def _add_vary(response: Response, header_name: str) -> None:
    """Append *header_name* to the response ``Vary`` header.

    ``Vary`` accepts a comma-separated list.  Append rather than
    replace so a route that already declared a different vary
    dimension is preserved, and skip the append when *header_name*
    is already present so repeated middleware passes (test reuse,
    nested calls) stay idempotent.
    """
    existing = response.headers.get("Vary", "")
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    if header_name not in parts:
        parts.append(header_name)
        response.headers["Vary"] = ", ".join(parts)

"""Serve Houndarr under the URL prefix set by ``HOUNDARR_URL_BASE``.

The proxy may forward or strip the prefix; :class:`BasePathMiddleware`
accepts both, plus unprefixed requests such as the container healthcheck.
Use :func:`route_path` to match app routes and :func:`app_url` for URLs
sent to the browser.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from fastapi import Request
from starlette.datastructures import URL
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class BasePathMiddleware:
    """Normalise every request onto ``base_path`` and keep redirects path-only."""

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.base_path = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Prefix the request path when the proxy stripped it, then delegate."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if self.base_path:
            scope = _rebase(scope, self.base_path)
        if scope["type"] == "http":
            send = _path_only_redirects(scope, send)
        await self.app(scope, receive, send)


def _rebase(scope: Scope, base: str) -> Scope:
    """Return *scope* with its path under *base* and ``root_path`` set to it.

    Leading slashes after the base collapse to one, so an nginx
    ``location /houndarr`` that strips into ``//login`` still routes.
    """
    path: str = scope["path"]
    has_base = path == base or path.startswith(f"{base}/")
    rest = path[len(base) :] if has_base else path
    rebased = {**scope, "path": f"{base}/{rest.lstrip('/')}", "root_path": base}

    raw_path: bytes | None = scope.get("raw_path")
    if raw_path is not None:
        raw_base = base.encode()
        raw_has_base = raw_path == raw_base or raw_path.startswith(raw_base + b"/")
        raw_rest = raw_path[len(raw_base) :] if raw_has_base else raw_path
        new_raw = raw_base + b"/" + raw_rest.lstrip(b"/")
        if unquote(new_raw.decode("latin-1")) == rebased["path"]:
            rebased["raw_path"] = new_raw
        else:
            # Percent-encoded base or slash bytes decode differently from the new
            # path; ASGI lets raw_path be absent, which beats a value that disagrees.
            del rebased["raw_path"]
    return rebased


def _path_only_redirects(scope: Scope, send: Send) -> Send:
    """Wrap *send* so a same-origin absolute ``Location`` becomes path-only.

    Starlette's trailing-slash redirect builds ``http://<Host>/...`` from the
    scope, which downgrades the scheme behind a TLS-terminating proxy.
    """

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start" and message["status"] in _REDIRECT_STATUSES:
            headers = [
                (name, _path_only(scope, value) if name.lower() == b"location" else value)
                for name, value in message.get("headers", [])
            ]
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


def _path_only(scope: Scope, location: bytes) -> bytes:
    """Return *location* without its origin when it points back at this request's host."""
    target = urlsplit(location.decode("latin-1"))
    if target.scheme not in ("http", "https") or not target.netloc:
        return location
    if target.netloc.lower() != URL(scope=scope).netloc.lower():
        return location
    # A path starting "//" or "/\" reads as another host once the origin is dropped.
    if not target.path.startswith("/") or target.path.startswith(("//", "/\\")):
        return location
    relative = target.path
    if target.query:
        relative += f"?{target.query}"
    if target.fragment:
        relative += f"#{target.fragment}"
    return relative.encode("latin-1")


def _app_root_path(request: Request) -> str:
    """Return the URL prefix; ``app_root_path`` survives a Mount extending ``root_path``."""
    scope = request.scope
    root: str = scope.get("app_root_path", scope.get("root_path", ""))
    return root


def route_path(request: Request) -> str:
    """Return the path the router matches (``/login``, not ``/houndarr/login``).

    Mirrors Starlette's segment-bounded ``get_route_path`` so auth classifies
    exactly the path the router dispatches on.  Reads the app's root, so the
    answer holds after a Mount extends ``root_path`` on the shared scope.
    """
    path: str = request.scope["path"]
    root = _app_root_path(request)
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    if path[len(root)] == "/":
        return path[len(root) :]
    return path


def app_url(request: Request, path: str) -> str:
    """Return *path* (an app route such as ``/login``) as the URL the browser must use."""
    return f"{_app_root_path(request)}{path}"

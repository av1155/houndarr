"""Serve Houndarr under the URL prefix set by ``HOUNDARR_URL_BASE``.

The proxy may forward or strip the prefix; :class:`BasePathMiddleware`
accepts both, plus unprefixed requests such as the container healthcheck.
Use :func:`route_path` to match app routes and :func:`app_url` for URLs
sent to the browser.
"""

from __future__ import annotations

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send


class BasePathMiddleware:
    """Normalise every request onto ``base_path`` and expose it as ``root_path``."""

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.base_path = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Prefix the request path when the proxy stripped it, then delegate."""
        base = self.base_path
        if not base or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        raw_path: bytes | None = scope.get("raw_path")
        if path == base:
            # Serve the bare prefix as the dashboard rather than a 404.
            path = f"{base}/"
            if raw_path is not None:
                raw_path += b"/"
        elif not path.startswith(f"{base}/"):
            path = f"{base}{path}"
            if raw_path is not None:
                raw_path = base.encode() + raw_path

        scope = {**scope, "path": path, "root_path": base}
        if raw_path is not None:
            scope["raw_path"] = raw_path
        await self.app(scope, receive, send)


def _app_root_path(request: Request) -> str:
    """Return the URL prefix; ``app_root_path`` survives a Mount extending ``root_path``."""
    scope = request.scope
    root: str = scope.get("app_root_path", scope.get("root_path", ""))
    return root


def route_path(request: Request) -> str:
    """Return the request path relative to the base path (``/login``, not ``/x/login``)."""
    root = _app_root_path(request)
    path = request.url.path
    if root and path.startswith(root):
        return path[len(root) :] or "/"
    return path


def app_url(request: Request, path: str) -> str:
    """Return *path* (an app route such as ``/login``) as the URL the browser must use."""
    return f"{_app_root_path(request)}{path}"

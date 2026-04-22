"""Shared helpers for HTMX-aware route handlers.

HTMX sets ``HX-Request: true`` on every request it issues.  Routes that
can be reached both directly (full page navigation or reload) and via
an HTMX swap call :func:`is_hx_request` to decide which template to
return: a full page with the shell for direct hits, or a partial for
HTMX to swap into ``#app-content``.
"""

from __future__ import annotations

from fastapi import Request


def is_hx_request(request: Request) -> bool:
    """Return True when *request* carries the ``HX-Request: true`` header."""
    return request.headers.get("HX-Request") == "true"

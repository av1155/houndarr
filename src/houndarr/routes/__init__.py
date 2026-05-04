"""FastAPI route handlers: HTMX page surface and JSON API.

Page modules render Jinja2 partials for the HTMX shell;
:mod:`~houndarr.routes.api` exposes JSON polling endpoints plus the
Docker ``/api/health`` probe.  Authentication is global via
:class:`~houndarr.auth.AuthMiddleware`; no per-route auth decorators.
"""

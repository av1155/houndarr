"""JSON API routes consumed by the dashboard and external widget.

:mod:`~houndarr.routes.api.status` returns the per-instance aggregate
bundle the dashboard polls; :mod:`~houndarr.routes.api.logs` returns
cursor-paginated ``search_log`` rows for the Logs page feed;
:mod:`~houndarr.routes.api.widget` returns the stable read-only widget
summary for external dashboards.
"""

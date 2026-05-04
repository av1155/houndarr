"""Service layer: business logic between routes and repositories.

Routes call into services; services call into
:mod:`houndarr.repositories` for SQL.  No service opens a database
connection directly, exposes a Pydantic wire model, or imports a route
module: those boundaries keep the layer testable without a running
FastAPI app.
"""

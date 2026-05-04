"""Async HTTP clients for the *arr APIs.

Each module subclasses :class:`~houndarr.clients.base.ArrClient` and owns
its app's endpoints, sort keys, and per-app domain dataclasses.  Wire
payloads are validated through :mod:`houndarr.clients._wire_models`
first, so a field rename upstream surfaces as a typed validation error
rather than a ``KeyError`` deep inside an adapter.
"""

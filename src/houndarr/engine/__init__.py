"""Search engine: per-instance asyncio cycle that drives the *arr APIs.

:mod:`~houndarr.engine.supervisor` runs one task per enabled instance;
:mod:`~houndarr.engine.search_loop` runs one cycle of missing / cutoff /
upgrade passes for one instance; :mod:`~houndarr.engine.adapters`
registers each *arr's :class:`AppAdapterProto` so the loop stays
per-app agnostic.
"""

"""Repository layer: module-per-aggregate SQL boundary.

Every module here implements one of the Protocols declared in
:mod:`houndarr.protocols`:

- ``settings.py``: key-value reads and writes for the ``settings``
  table.
- ``instances.py``: ``instances`` table CRUD with
  :class:`InstanceInsert` and :class:`InstanceUpdate` payload
  dataclasses.
- ``cooldowns.py``: per-item cooldown upsert, existence check, and
  per-instance delete.  The LRU skip-log sentinel stays in
  :mod:`houndarr.services.cooldown`; it is an in-process cache, not
  a SQL boundary.
- ``search_log.py``: insert, filtered page fetch, recent-searches
  counter, per-instance delete, and retention purge for the
  ``search_log`` table; the engine's ``_write_log`` helper is a thin
  call into ``insert_log_row``.

Every module is function-based (no classes) and scoped to a single
aggregate.  Callers depend on the structural Protocol shape, not on
the concrete repository import, which keeps the boundary
test-swappable without subclass gymnastics.

Schema migrations (``_migrate_to_v2`` through ``_migrate_to_v13``)
stay in :mod:`houndarr.database` and are off-limits here; repository
functions only wrap ``get_db()`` queries and never alter schema,
PRAGMAs, or the WAL lifecycle.
"""

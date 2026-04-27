"""Repository layer: module-per-aggregate SQL boundary.

Track D.1 introduces this namespace.  Concrete modules land
incrementally over the remaining D batches and implement the
Protocols already declared in :mod:`houndarr.protocols`:

- ``settings.py`` (D.2): key-value reads and writes, replacing the
  ``get_setting`` / ``set_setting`` helpers inside ``database.py``.
- ``instances.py`` (D.3 reads, D.4 writes): ``instances`` table CRUD
  with ``InstanceInsert`` + ``InstanceUpdate`` payload dataclasses.
- ``cooldowns.py`` (D.5): ``cooldowns`` table SQL.  The LRU skip-log
  sentinel stays in :mod:`houndarr.services.cooldown` and is not
  part of this boundary.
- ``search_log.py`` (D.6): insert, filtered page fetch, recent
  searches counter, and per-instance delete for the ``search_log``
  table; the engine's ``_write_log`` helper becomes a thin call into
  ``insert_log_row``.

Per locked user decision #4, every module in this package is
function-based (no classes) and scoped to a single aggregate.
Callers depend on the structural :mod:`houndarr.protocols` shape,
not on the concrete repository import, which keeps the boundary
test-swappable without subclass gymnastics.

The migration helpers ``_migrate_to_v2`` through ``_migrate_to_v13``
stay off-limits in :mod:`houndarr.database`; repository functions
only wrap ``get_db()`` queries and never alter schema, PRAGMAs, or
the WAL lifecycle.
"""

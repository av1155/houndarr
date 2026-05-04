---
name: houndarr-database
description: Houndarr's SQLite schema and migration discipline. Loads when reading or editing src/houndarr/database.py. Covers the schema table reference, the SCHEMA_VERSION bump checklist, and the version-locking rule for migration constants that prevents later renames from retroactively breaking earlier rebuild migrations.
paths:
  - "src/houndarr/database.py"
---

# Houndarr database conventions

## Database basics

SQLite via aiosqlite. `get_db()` is an async context manager that opens
a fresh connection per call (FKs enabled per connection; WAL mode set
once in `init_db()`). Schema version is currently 13. Bump
`SCHEMA_VERSION` and add a `_migrate_to_vN` when changing schema.

## Schema reference

| Table | Purpose | Key constraints |
|-------|---------|-----------------|
| `settings` | Key-value config store | `key TEXT PK` |
| `instances` | *arr instance configs | `type CHECK IN ('radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3')`; many policy columns with CHECK constraints; `monitored_total` / `unreleased_count` / `snapshot_refreshed_at` populated by the supervisor's snapshot refresh task |
| `cooldowns` | Per-item search cooldown tracking | `instance_id FK→instances ON DELETE CASCADE`; `UNIQUE(instance_id, item_id, item_type)`; `search_kind CHECK IN ('missing','cutoff','upgrade')` (v15) |
| `search_log` | Audit trail | `instance_id FK→instances ON DELETE SET NULL`; `action CHECK IN ('searched','skipped','error','info')` |

Full DDL and migrations live in `src/houndarr/database.py`.

## Migration constants are version-locked

Rebuild migrations (`CREATE TABLE foo_new ... INSERT INTO foo_new SELECT ...`)
must reference a snapshot constant frozen at the introducing schema version,
never the current `_ITEM_TYPES` / `_INSTANCE_TYPES` alias. The snapshots
(`_ITEM_TYPES_V5`, `_ITEM_TYPES_V10`, `_ITEM_TYPES_V15`, `_ITEM_TYPES_V16`,
`_INSTANCE_TYPES_V5`, `_INSTANCE_TYPES_V10`) live at the top of `database.py`
and are immutable after their migration ships. Fresh-install DDL in
`_SCHEMA_SQL` uses the latest snapshot via the `_ITEM_TYPES` /
`_INSTANCE_TYPES` aliases.

When adding a migration that renames a value: introduce a new
`_FOO_TYPES_VN` constant, point the `_FOO_TYPES` alias at it, write the new
migration with the new constant plus a CASE WHEN translation in its COPY,
and leave the prior snapshot (and prior migrations) untouched. This prevents
the class of bug where a later rename retroactively breaks an earlier
rebuild migration's CHECK clause.

## Schema bump checklist

1. Bump `SCHEMA_VERSION` at the top of `database.py`.
2. Add a `_migrate_to_vN(conn)` function that performs the migration.
3. If the migration renames a value used in CHECK constraints, introduce
   a fresh `_FOO_TYPES_VN` snapshot and reference it in the new migration.
4. Add an entry to `_MIGRATIONS` keyed by the new version.
5. Update `_SCHEMA_SQL` if the canonical fresh-install DDL changed.
6. Add tests under `tests/test_database/` covering the migration path.

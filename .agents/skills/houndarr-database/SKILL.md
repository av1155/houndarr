---
name: houndarr-database
description: Houndarr's SQLite schema and migration discipline. Loads when reading or editing src/houndarr/database.py. Covers the schema table reference, the SCHEMA_VERSION bump checklist, and the version-locking rule for migration constants that prevents later renames from retroactively breaking earlier rebuild migrations.
paths:
  - "src/houndarr/database.py"
---

# Houndarr database conventions

## Database basics

SQLite via aiosqlite. `get_db()` is an async context manager that
borrows a connection from a per-path pool (`_pools`, size 10);
`_connection_factory` sets WAL, `foreign_keys=ON` and the other PRAGMAs
once per pool member, so a connection keeps any PRAGMA a caller changes
when it goes back to the pool. Schema version is currently 23. Bump
`SCHEMA_VERSION` and add a `_migrate_to_vN` when changing schema.

## Schema reference

| Table | Purpose | Key constraints |
|-------|---------|-----------------|
| `settings` | Key-value config store | `key TEXT PK` |
| `widget_api_key` | External widget API key, single row (v19) | `id INTEGER PRIMARY KEY CHECK(id = 1)`; `hash` is SHA-256 hex digest with `CHECK(length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*')`; no FK |
| `instances` | *arr instance configs | `type CHECK IN ('radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3')`; CHECKs on the eight search modes, `upgrade_series_window_size` (1 to 100) and `search_order` (on every database since v23); `monitored_total` / `unreleased_count` / `snapshot_refreshed_at` populated by the supervisor's snapshot refresh task |
| `cooldowns` | Per-item search cooldown tracking | `instance_id FK→instances ON DELETE CASCADE`; `UNIQUE(instance_id, item_id, item_type)`; `search_kind CHECK IN ('missing','cutoff','upgrade')` (v15) |
| `search_log` | Audit trail | `instance_id FK→instances ON DELETE SET NULL`; `action CHECK IN ('searched','skipped','error','info')` |

Full DDL and migrations live in `src/houndarr/database.py`.

## Migration constants are version-locked

Rebuild migrations (`CREATE TABLE foo_new ... INSERT INTO foo_new SELECT ...`)
must reference a snapshot constant frozen at the introducing schema version,
never the current `_ITEM_TYPES` / `_INSTANCES_COLUMNS` alias. The snapshots
(`_ITEM_TYPES_V5`, `_ITEM_TYPES_V10`, `_ITEM_TYPES_V15`, `_ITEM_TYPES_V16`,
`_INSTANCE_TYPES_V5`, `_INSTANCE_TYPES_V10`, and the whole `instances`
column block `_INSTANCES_COLUMNS_V23` with its name tuple
`_INSTANCES_COLUMN_NAMES_V23`) live at the top of `database.py` and are
immutable after their migration ships. Fresh-install DDL in `_SCHEMA_SQL`
uses the latest snapshot via the `_ITEM_TYPES` / `_INSTANCES_COLUMNS`
aliases, so a fresh `instances` table and one rebuilt by v23 are identical
(`test_migrated_instances_table_matches_a_fresh_one` checks every seed shape).

When adding a migration that renames a value: introduce a new
`_FOO_TYPES_VN` constant, point the `_FOO_TYPES` alias at it, write the new
migration with the new constant plus a CASE WHEN translation in its COPY,
and leave the prior snapshot (and prior migrations) untouched. This prevents
the class of bug where a later rename retroactively breaks an earlier
rebuild migration's CHECK clause. A change to the `instances` columns or
their CHECKs gets a new `_INSTANCES_COLUMNS_VN` the same way.

`ALTER TABLE ADD COLUMN` accepts a CHECK clause; write the fresh DDL's
CHECK into the ALTER. v12 and v17 left theirs out, which is why v23 had to
rebuild `instances`. A CHECK cannot be added to a column that already
exists.

## Rebuild migrations

A rebuild (`CREATE foo_new`, copy, `DROP foo`, `RENAME`) must:

- `commit()` first, then `PRAGMA foreign_keys=OFF`: the PRAGMA is a no-op
  inside a transaction, and a DROP with FKs on cascades to child rows.
- Run inside an explicit `BEGIN`: Python's sqlite3 opens a transaction only
  before DML, so CREATE, DROP and RENAME would otherwise commit on their own.
- `DROP TABLE IF EXISTS foo_new` first, so a crashed earlier run can retry.
- Copy every column by name, never `SELECT *`.
- Keep the AUTOINCREMENT high-water mark: read `sqlite_sequence` before the
  DROP and write back `MAX(seq, old)` after the RENAME, or a new row can
  reuse a deleted row's id.
- Repair rows a foreign-key-off connection could have orphaned, then require
  `PRAGMA foreign_key_check` to come back empty before the COMMIT.
- Turn FKs back on in a `finally`, after the commit or rollback.
  `init_db_migrations` also rolls back and re-enables FKs on its connection,
  because v5, v10, v15 and v16 run their `foreign_keys=ON` inside a
  transaction, where it does nothing, and the connection returns to the pool.

A rebuild that copies every column of a table (v23) runs only from the
self-heal list in `init_db_migrations`, after every migration that adds a
column it copies: only that pass guarantees those columns exist on a
database stamped ahead of its columns. A later migration on that table goes
after it in the self-heal list.

## Schema bump checklist

1. Bump `SCHEMA_VERSION` at the top of `database.py`.
2. Add a `_migrate_to_vN(conn)` function that performs the migration.
3. If the migration renames a value used in CHECK constraints, introduce
   a fresh `_FOO_TYPES_VN` snapshot and reference it in the new migration.
4. Call it from `_run_migrations` (`if from_version < N:`) and from the
   self-heal list in `init_db_migrations`, so a database stamped at the
   current version but missing the columns is repaired. A rebuild that
   copies every column goes in the self-heal list only (see Rebuild
   migrations).
5. Update `_SCHEMA_SQL` if the canonical fresh-install DDL changed.
6. Add tests in `tests/test_database.py` for the fresh DDL, the
   `N-1` to `N` migration, and the self-heal path, and move its
   `schema_version == "N-1"` pins to `"N"`.

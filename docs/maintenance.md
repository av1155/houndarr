# Maintenance

Operational notes for maintainers, separate from the user-facing docs site.
Not intended for end users.

## Single-maintainer dependencies

Houndarr pins two single-maintainer dependencies at exact versions:

| Package | Pin | Source | Role |
|---|---|---|---|
| `aiosqlitepool` | `==1.0.0` | github.com/slaily/aiosqlitepool | Pool wrapper around `aiosqlite.connect()` used by every `get_db()` borrow |
| `async-lru` | `==2.3.0` | github.com/aio-libs/async-lru | Backs the `/api/status` aggregate cache |

Both are pure-Python packages; neither has compiled wheels. Both passed
`pip-audit` at adoption time.

`async-lru` is maintained by `aio-libs` (the asyncio working group behind
aiohttp) and has the same maintenance posture as the rest of that org.
`aiosqlitepool` is single-maintainer, which is why it gets the
**vendoring fallback plan** below.

### When to consider vendoring `aiosqlitepool`

Either of the following is a hard signal:

- Upstream stops responding to security advisories (CVE-affecting transitive
  bumps, Python release support windows).
- A breaking change ships in a `1.x.y` patch (sane projects do not do this,
  but pinning to `==1.0.0` means we will notice).

Soft signals worth opening a tracking issue for, but not yet acting on:

- The repo goes 6+ months without a commit.
- `pip-audit` flags `aiosqlitepool` directly (rather than a transitive).

### Vendoring procedure

When the time comes:

1. Vendor the source under `src/houndarr/_vendor/aiosqlitepool/`. The
   library is small (~200 lines, one `pool.py` plus an `__init__.py`).
   The `_wire_models/` package is the precedent for in-tree vendoring
   inside the `houndarr` namespace.
2. Replace the import in `src/houndarr/database.py`:

   ```python
   # before
   from aiosqlitepool import SQLiteConnectionPool
   # after
   from houndarr._vendor.aiosqlitepool import SQLiteConnectionPool
   ```

3. Drop `aiosqlitepool` from `requirements.txt` and `pyproject.toml`.
4. Drop the `aiosqlitepool.*` entry from the mypy ignore-missing-imports
   override in `pyproject.toml`.
5. Add a `tests/test_vendor_aiosqlitepool.py` smoke test that exercises
   the pool's `acquire`/`release`/`close` paths against an in-memory
   SQLite connection. The library's own test suite is small enough to
   port wholesale; pinning their tests as ours catches future
   maintainer-facing regressions.
6. Update this doc with the date, the upstream commit SHA the vendored
   copy was forked from, and a one-line reason. Future-you needs to
   know which fixes were already applied versus which need backporting
   from upstream.

### When NOT to vendor

If the only complaint is "the maintainer is slow", do not vendor. The
library's API surface is tiny (`SQLiteConnectionPool` constructor plus
`connection()`, `close()`); we already pin to an exact version, and
slow maintenance is irrelevant when the API is not changing. Vendoring
is the **escape hatch**, not the default.

## Connection-pool error semantics

`aiosqlitepool.SQLiteConnectionPool.release()` already calls
`conn.reset()` (which runs `rollback()`) on every release. If `reset()`
raises (broken connection, transaction stuck open), the connection is
retired and closed inside the pool. Houndarr's `get_db()` does NOT
need its own try/except wrapper for connection disposal; the library
handles it natively. See `aiosqlitepool/pool.py:Pool.release` for the
canonical implementation.

This is documented here because the audit pass on issue #586 raised
the disposal question and it is a recurring "we should add custom
error handling around the pool" instinct. The answer is no, the
library already does it.

## SQLite operational PRAGMA stack

The connection factory in `src/houndarr/database.py:_connection_factory`
applies the modern operational PRAGMA stack on every pool member at
factory time, not on every borrow. The reasoning lives in the
docstring; do not duplicate it here. The one constraint worth knowing
out-of-line: `journal_mode=WAL` must precede `synchronous=NORMAL`
because `NORMAL` is only corruption-safe in WAL mode. The factory
already orders them correctly.

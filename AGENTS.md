# AGENTS.md — Houndarr

Coding-agent reference for the Houndarr repository.
Python 3.12+ / FastAPI / SQLite / HTMX self-hosted media search companion.

---

## Build & Run

```bash
# Setup
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

# Run locally (dev mode)
.venv/bin/python -m houndarr --data-dir ./data-dev --dev
```

## Quality Gates (run all before every commit)

```bash
.venv/bin/python -m ruff check src/ tests/          # lint
.venv/bin/python -m ruff format --check src/ tests/  # format check
.venv/bin/python -m mypy src/                        # type check (strict)
.venv/bin/python -m bandit -r src/ -c pyproject.toml # SAST
.venv/bin/pytest                                     # all tests
```

## Running Tests

```bash
# Full suite (201 tests, async)
.venv/bin/pytest

# Single test file
.venv/bin/pytest tests/test_auth.py

# Single test by name
.venv/bin/pytest tests/test_auth.py::test_check_password_valid -v

# Single test directory
.venv/bin/pytest tests/test_services/

# With coverage
.venv/bin/pytest --cov=houndarr --cov-report=term-missing
```

## CI Checks (all required checks must pass before merge)

| Workflow | Command |
|----------|---------|
| quality | `ruff check .` / `ruff format --check .` / `mypy src/` |
| tests | `pytest -q --tb=short` (Python 3.12) |
| security | `pip-audit` / `bandit -r src/ -c pyproject.toml` |
| docker | multi-arch build (amd64/arm64); push on `v*` tags only |
| dockerfile-lint | `hadolint Dockerfile` |
| workflow-lint | `actionlint` |

Branch protection currently expects 7 check runs from these workflows.

---

## Code Style

### Formatting

- **Line length:** 100 characters
- **Indentation:** 4 spaces (2 for YAML/JSON/TOML)
- **Target Python:** 3.12+ (`pyproject.toml` sets `target-version = "py312"`)
- **Formatter/linter:** Ruff — rules `E W F I B C4 UP SIM ANN S N`

### Imports (every file, no exceptions)

```python
from __future__ import annotations          # ALWAYS first line

# 1. Standard library
import logging
from pathlib import Path
from typing import Any

# 2. Third-party
import httpx
from fastapi import APIRouter, Request

# 3. First-party
from houndarr.config import get_settings
from houndarr.database import get_db
```

- isort via Ruff; `known-first-party = ["houndarr"]`
- `from __future__ import annotations` is mandatory in every `.py` file

### Type Annotations

- **mypy strict mode** — all public functions need full signatures
- Use modern union syntax: `str | None`, not `Optional[str]`
- Use builtin generics: `list[str]`, `dict[str, Any]`, not `List`/`Dict`
- Use `collections.abc.AsyncGenerator`, not `typing.AsyncGenerator`
- Specific `# type: ignore[error-code]` when needed; never bare `# type: ignore`
- Tests are exempt from `ANN` rules (per-file-ignores)

### Naming Conventions

| Kind | Style | Example |
|------|-------|---------|
| Classes / dataclasses | PascalCase | `SonarrClient`, `MissingEpisode` |
| Functions / methods | snake_case | `create_instance`, `run_instance_search` |
| Private functions | `_leading_underscore` | `_write_log`, `_parse_episode` |
| Constants | UPPER_SNAKE_CASE | `SESSION_MAX_AGE_SECONDS`, `SCHEMA_VERSION` |
| Module-level state | `_leading_underscore` | `_db_path`, `_runtime_settings` |
| Enums | StrEnum, lowercase values | `InstanceType.sonarr` |
| Type aliases | PascalCase or Literal | `ItemType = Literal["episode", "movie"]` |

### Docstrings

- Module-level docstring on every file
- Google-style for functions: `Args:`, `Returns:`, `Raises:` sections
- Test functions may have brief single-line docstrings

### Error Handling

- Background tasks: broad `except Exception` with `# noqa: BLE001`, log + continue
- HTTP clients: `response.raise_for_status()` — callers handle `httpx.HTTPError`
- `asyncio.CancelledError`: always catch and re-raise
- Auth helpers: catch-all returns `False` (never leaks info)

### Known noqa / nosec Suppressions

| Code | Reason |
|------|--------|
| `SIM117` | Nested `async with` required by aiosqlite |
| `S104` / `B104` | Intentional bind to `0.0.0.0` for self-hosted server |
| `S101` / `B101` | Asserts used in non-test code (post-insert sanity) |
| `B008` | FastAPI `Depends()` in function defaults |
| `S608` / `B608` | Dynamic SQL with explicit column allowlist |
| `BLE001` | Broad exception in background loops |
| `PLW0603` | Module-level global reassignment (settings/db singletons) |

---

## Testing Patterns

- **Framework:** pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **Async tests:** decorated with `@pytest.mark.asyncio()` (with parens), return `-> None`
- **HTTP mocking:** `respx` for httpx calls — use `@respx.mock` decorator
- **App testing:** `TestClient` (sync) or `AsyncClient` via `ASGITransport` (async)
- **Fixtures hierarchy:** `tmp_data_dir` -> `db` -> `test_settings` -> `app`/`async_client`

### FK constraint pattern (cooldown / engine tests)

Tests touching `cooldowns` or `search_log` must seed the `instances` table first:

```python
@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [(1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
             (2, "Radarr Test", "radarr", "http://radarr:7878")],
        )
        await conn.commit()
    yield
```

Use `seeded_instances` (not bare `db`) as the fixture dependency in those tests.
Engine tests also set `encrypted_api_key` to a valid Fernet-encrypted value.

### Login helper for route tests

```python
def _login(client: TestClient) -> None:
    client.post("/setup", data={"username": "admin", "password": "ValidPass1!", ...})
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})
```

### CSRF helper for route tests

All mutating route tests (POST, DELETE) require a valid CSRF token after login.
Use the helpers from `tests/conftest.py`:

```python
from tests.conftest import csrf_headers, get_csrf_token

# Pass as headers kwarg to client.post/client.delete:
resp = client.post("/settings/instances", data=form, headers=csrf_headers(client))
resp = client.delete("/settings/instances/1", headers=csrf_headers(client))
```

The CSRF cookie (`houndarr_csrf`) is set automatically when `_login` runs.
The `test_settings` fixture also resets `_auth._serializer` and
`_auth._login_attempts` so auth state doesn't bleed between tests.

---

## Architecture Notes

- **Encryption key:** `request.app.state.master_key` — pass explicitly to services
- **HTMX partials:** `hx-swap="outerHTML"` / `"innerHTML"`, no full-page reloads
- **Supervisor:** one `asyncio.Task` per enabled instance; 10s shutdown timeout
- **search_log:** every search attempt writes a row (`searched`/`skipped`/`error`/`info`)
- **Database:** SQLite via `aiosqlite`; schema version 1; `get_db()` context manager

## Workflow

- Branch naming: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`
- Commits: Conventional Commits (`feat:`, `fix:`, `ci:`, `chore:`, etc.)
- PRs: squash-merge only; all required CI checks green before merge
- If mypy CI fails with "merge ref not found": push empty commit to retrigger

### Staged execution discipline (required)

1. Investigate and define a tight scope before editing code.
2. Create a GitHub issue first with clear acceptance criteria.
3. Apply mandatory labels on the issue before implementation starts.
4. Create a scoped branch (`type/short-slug`) for that issue only.
5. Implement only issue-scoped changes; avoid mixed concerns.
6. Run all local quality gates before committing.
7. Open a scoped PR with native linking (`Closes #N`).
8. Merge only after all required checks are green.
9. Housekeeping after merge: sync `main`, delete branch, prune refs.

### Issue label policy (required)

Every issue must have:
- Exactly one `type:*` label
- Exactly one `priority:*` label
- At most one `phase:*` label (required for roadmap/product delivery work)

#### Type labels

- `type: bug` — incorrect behavior, regressions, broken UX
- `type: feature` — user-facing capability additions
- `type: docs` — documentation-only work
- `type: chore` — maintenance, refactors, tooling, process
- `type: test` — test-only additions/changes
- `type: ci` — workflow/pipeline automation changes
- `type: security` — vulnerability or security hardening work

#### Priority labels

- `priority: high` — release-blocking, data-risk, security, or urgent breakage
- `priority: medium` — default for normal planned work
- `priority: low` — optional improvements and non-urgent polish

#### Phase labels

- `phase: 0-workflow` — process, templates, policy, governance
- `phase: 1-foundation` to `phase: 6-release` — roadmap execution phases

#### Deprecated generic labels

Use namespaced `type:*` labels only. Legacy generic labels are deprecated:
- `bug`
- `enhancement`
- `documentation`

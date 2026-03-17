# AGENTS.md — Houndarr

Coding-agent reference for the Houndarr repository.
This file is the primary source of truth for autonomous agents operating here.

## Project Overview

Houndarr is a self-hosted companion for Sonarr, Radarr, Lidarr, Readarr, and
Whisparr that automatically searches for missing and cutoff-unmet media in
small, rate-limited batches. It runs as a single Docker container alongside
an existing *arr stack.

**Tech stack:** Python 3.12 / FastAPI / aiosqlite (SQLite) / Jinja2 / HTMX /
Tailwind CSS CDN. Published to GHCR at `ghcr.io/av1155/houndarr`.

**Scope guard:** Houndarr is a single-purpose tool. Every change must help
search for missing or cutoff-unmet media in a controlled, polite way.
Do not add download-client integration, indexer management, request workflows,
multi-user support, or media file manipulation.

---

## Setup & Run

```bash
# Create venv and install
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

# Run locally (dev mode — auto-reload, API docs at /docs)
.venv/bin/python -m houndarr --data-dir ./data-dev --dev
```

Dev server: `http://localhost:8877`.

---

## Quality Gates

Run **all five** before every commit. CI enforces the same checks.

```bash
.venv/bin/python -m ruff check src/ tests/          # lint
.venv/bin/python -m ruff format --check src/ tests/  # format check
.venv/bin/python -m mypy src/                        # type check (strict)
.venv/bin/python -m bandit -r src/ -c pyproject.toml # SAST
.venv/bin/pytest                                     # all tests
```

---

## Running Tests

```bash
# Full suite (408 tests, async — count includes parametrised expansions)
.venv/bin/pytest

# Single file
.venv/bin/pytest tests/test_auth.py

# Single test by name
.venv/bin/pytest tests/test_auth.py::test_check_password_valid -v

# Tests matching a keyword expression
.venv/bin/pytest -k "csrf" -v

# Single directory
.venv/bin/pytest tests/test_services/

# With coverage
.venv/bin/pytest --cov=houndarr --cov-report=term-missing
```

**Pytest config** (from `pyproject.toml`):

- `asyncio_mode = "auto"` — async tests run without manual event-loop setup
- `asyncio_default_fixture_loop_scope = "function"` — each test gets its own loop
- `addopts = "-q --tb=short"` — default quiet output with short tracebacks

---

## CI Checks

### Required checks (10 — branch protection enforced)

| Check name | Workflow file | What it runs |
|------------|---------------|--------------|
| Lint (ruff) | `quality.yml` | `ruff check .` |
| Format (ruff) | `quality.yml` | `ruff format --check .` |
| Type check (mypy) | `quality.yml` | `mypy src/` |
| Test (Python 3.12) | `tests.yml` | `pytest -q --tb=short` + compile check + `--help` |
| Dependency audit (pip-audit) | `security.yml` | `pip-audit -r requirements.txt -r requirements-dev.txt` |
| SAST (bandit) | `security.yml` | `bandit -r src/ -c pyproject.toml` |
| Trivy filesystem scan | `security.yml` | `trivy fs .` — CRITICAL/HIGH with known fix |
| Dependency review | `dependency-review.yml` | PR dependency diff vs GitHub Advisory Database |
| Build (no push) | `docker.yml` | Multi-arch Docker build (amd64/arm64), no push |
| Trivy image scan | `docker.yml` | Trivy scan of built Docker image — CRITICAL/HIGH with known fix |

The five main workflows (`quality`, `tests`, `security`, `dependency-review`,
`docker`) use `paths-ignore: ["docs/**", "*.md", "website/**"]`. When a PR
touches only those paths, `ci-skip.yml` provides passing no-op jobs with
identical check names so branch protection is satisfied.

### Additional workflows (not required checks)

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `version-check.yml` | PRs changing `VERSION` or `CHANGELOG.md` | Validates VERSION format, CHANGELOG heading match, allowed `###` headers, `---` separator |
| `release.yml` | `v*` tag push | Validates VERSION == tag, extracts CHANGELOG block, creates GitHub Release |
| `dockerfile-lint.yml` | Changes to `Dockerfile` | `hadolint Dockerfile` |
| `workflow-lint.yml` | Changes to `.github/workflows/**` | `actionlint` via reviewdog |
| `api-snapshot-refresh.yml` | Weekly (Monday 10:00 UTC) + manual | Fetches upstream Sonarr/Radarr/Whisparr/Lidarr/Readarr OpenAPI specs, updates `docs/api/` snapshots and `tests/test_docs_api.py` hashes, opens a PR if changed |
| `pages.yml` | Pushes to `main` touching `website/**` | Deploys docs site to GitHub Pages |
| `test-deploy.yml` | PRs touching `website/**` | Tests Docusaurus build without deploying |
| `cleanup-actions-cache.yml` | Daily (05:00 UTC) + manual | Prunes stale GitHub Actions caches |

### Branch protection on `main`

- 10 required status checks (strict — branch must be up to date)
- Required PR reviews enabled (dismiss stale reviews, required conversation resolution)
- Linear history enforced (no merge commits)
- No force pushes, no branch deletions
- Enforce admins enabled
- CODEOWNERS: `@av1155` owns all files

---

## Code Style

### Formatting

- **Line length:** 100 characters
- **Indentation:** 4 spaces (2 for YAML/JSON/TOML)
- **Target Python:** 3.12+ (`target-version = "py312"` in `pyproject.toml`)
- **Linter/formatter:** Ruff — selected rule sets: `E W F I B C4 UP SIM ANN S N`

### Imports

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

- `from __future__ import annotations` is mandatory in every `.py` file that
  contains code. Empty `__init__.py` package markers are exempt.
- isort via Ruff; `known-first-party = ["houndarr"]`

### Type Annotations

- **mypy strict mode** — all public functions need full signatures
- Modern union syntax: `str | None`, not `Optional[str]`
- Builtin generics: `list[str]`, `dict[str, Any]`, not `List`/`Dict`
- `collections.abc.AsyncGenerator`, not `typing.AsyncGenerator`
- Specific error codes: `# type: ignore[assignment]` — never bare `# type: ignore`
- Tests are exempt from `ANN` rules (per-file-ignores in `pyproject.toml`)

### Naming Conventions

| Kind | Style | Example |
|------|-------|---------|
| Classes / dataclasses | PascalCase | `SonarrClient`, `AppSettings` |
| Functions / methods | snake_case | `create_instance`, `run_instance_search` |
| Private helpers | `_leading_underscore` | `_write_log`, `_render` |
| Constants | UPPER_SNAKE_CASE | `SESSION_MAX_AGE_SECONDS`, `SCHEMA_VERSION` |
| Module-level state | `_leading_underscore` | `_db_path`, `_runtime_settings` |
| Enums | `StrEnum`, lowercase values | `InstanceType.sonarr` |
| Type aliases | PascalCase or Literal | `ItemType = Literal["episode", "movie", "album", "book", "whisparr_episode"]` |

### Docstrings

- Module-level docstring on every file that contains code
- Google-style for functions: `Args:`, `Returns:`, `Raises:` sections
- Test functions may use brief single-line docstrings

### Logging

Every module that logs uses `logger = logging.getLogger(__name__)` at module
level. Root logger is configured in `__main__.py` via `logging.basicConfig()`.
No alternative logging libraries (structlog, loguru) are used.

### Error Handling

- **Background tasks:** `except asyncio.CancelledError: raise` first, then
  broad `except Exception` with `# noqa: BLE001` — log + continue/retry
- **HTTP clients:** `response.raise_for_status()` in `_get()`/`_post()`;
  callers catch `httpx.HTTPError` or `httpx.TransportError`
- **Auth helpers:** catch-all returns `False` (never leaks info)
- **Routes:** return re-rendered templates with `status_code=422` for
  validation errors; use `HTTPException` in API routes

### Known `noqa` / `nosec` Suppressions

| Code | Reason |
|------|--------|
| `SIM117` | Nested `async with` required by aiosqlite pattern |
| `S104` | Intentional bind to `0.0.0.0` for self-hosted server |
| `B008` | FastAPI `Depends()` in function defaults |
| `S608` + `nosec B608` | Dynamic SQL with explicit column allowlist (3 files) |
| `BLE001` | Broad exception in background loops (always with logging) |
| `A002` | Parameter names `type`/`id` shadowing builtins (FastAPI form/function signature convention) |
| `SLF001` | Test fixtures and `__main__.py` accessing private module state |
| `PLW0603` | Module-level global reassignment (singletons) — note: the `PLW` rule family is not currently selected in ruff config, so these comments are defensive/inert |
| `S101` | Defensive assert in adapters and instance validation — also globally ignored in ruff config; per-file comments are defensive |

---

## Architecture

### Source layout

```
src/houndarr/
  __main__.py          # CLI entry point (Click), logging setup, uvicorn.run
  app.py               # create_app(), lifespan, middleware registration
  auth.py              # AuthMiddleware, bcrypt, CSRF, rate limiter
  config.py            # AppSettings dataclass, get_settings() singleton
  crypto.py            # Fernet encrypt/decrypt, master key management
  database.py          # get_db() context manager, schema migrations
  clients/             # httpx-based *arr API clients
    base.py            # ArrClient ABC with _get()/_post() + raise_for_status()
    sonarr.py          # SonarrClient (episode/season search, v3 API)
    radarr.py          # RadarrClient (movie search, v3 API)
    lidarr.py          # LidarrClient (album/artist search, v1 API)
    readarr.py         # ReadarrClient (book/author search, v1 API)
    whisparr.py        # WhisparrClient (episode/season search, v3 API)
  engine/
    candidates.py      # SearchCandidate dataclass, ItemType, date helpers
    search_loop.py     # run_instance_search() — unified search pipeline
    supervisor.py      # Supervisor — one asyncio.Task per enabled instance
    adapters/
      __init__.py      # AppAdapter dataclass, ADAPTERS registry, get_adapter()
      sonarr.py        # Sonarr adapter: candidate conversion + dispatch
      radarr.py        # Radarr adapter: candidate conversion + dispatch
      lidarr.py        # Lidarr adapter: candidate conversion + dispatch
      readarr.py       # Readarr adapter: candidate conversion + dispatch
      whisparr.py      # Whisparr adapter: candidate conversion + dispatch
  routes/
    pages.py           # Setup, Login, Dashboard, Logs, Settings page routes
    settings.py        # Settings CRUD (instance add/edit/delete/toggle)
    health.py          # GET /api/health (Docker HEALTHCHECK)
    api/
      logs.py          # GET /api/logs (JSON, with cursor-based pagination)
      status.py        # GET /api/status (JSON, dashboard polling)
  services/
    instances.py       # Instance CRUD, InstanceType StrEnum
    cooldown.py        # Per-item search cooldown tracking
    url_validation.py  # SSRF guard for instance URLs
```

### Key patterns

- **Database:** SQLite via aiosqlite; schema version 5; `get_db()` async
  context manager opens a fresh connection per call (WAL mode, FKs enabled)
- **Config:** `AppSettings` is a plain dataclass (not Pydantic); `get_settings()`
  is a lazy singleton
- **Encryption:** Master key in `request.app.state.master_key`; passed
  explicitly to service functions as `master_key=` kwarg — never imported globally
- **Auth:** Global `AuthMiddleware` (Starlette `BaseHTTPMiddleware`) handles
  session validation and CSRF enforcement; no per-route auth decorators
- **HTMX:** SPA-like shell navigation — nav links use `hx-target="#app-content"`
  with `hx-swap="innerHTML"` and `hx-push-url="true"`. Routes check
  `_is_hx_request(request)` and return either partial or full template.
  Templates are lazily initialised via a module-level singleton
- **Supervisor:** One `asyncio.Task` per enabled instance; 10s shutdown timeout
- **search_log:** Every search attempt writes a row with action
  `searched`/`skipped`/`error`/`info`

### Database schema (SQLite, schema version 5)

| Table | Purpose | Key constraints |
|-------|---------|-----------------|
| `settings` | Key-value config store | `key TEXT PK` |
| `instances` | *arr instance configs | `type CHECK IN ('sonarr','radarr','lidarr','readarr','whisparr')`; per-type `*_search_mode` columns with CHECK constraints |
| `cooldowns` | Per-item search cooldown tracking | `instance_id FK→instances ON DELETE CASCADE`; `UNIQUE(instance_id, item_id, item_type)` |
| `search_log` | Audit trail for every search cycle | `instance_id FK→instances ON DELETE SET NULL`; `action CHECK IN ('searched','skipped','error','info')` |

Full DDL, column definitions, indexes, and migrations (`_migrate_to_v2`
through `_migrate_to_v5`) are in `src/houndarr/database.py`. Bump
`SCHEMA_VERSION` when adding new migrations.

### *arr API reference (local)

Full upstream OpenAPI specs are vendored locally and kept current:

- `docs/api/sonarr_openapi.json` — Sonarr v3 API (OpenAPI 3.0.1)
- `docs/api/radarr_openapi.json` — Radarr v3 API (OpenAPI 3.0.4)
- `docs/api/whisparr_openapi.json` — Whisparr v3 API (OpenAPI 3.0.1)
- `docs/api/lidarr_openapi.json` — Lidarr v1 API (OpenAPI 3.0.4)
- `docs/api/readarr_openapi.json` — Readarr v1 API (OpenAPI 3.0.1)

**Use these as the source of truth** when modifying or creating *arr client
code. They document every endpoint, parameter, request body, and response
schema. See `docs/api/README.md` for usage guidelines.

All five specs are actively used by their respective clients in `clients/`.

**Freshness:** `api-snapshot-refresh.yml` auto-fetches all five upstream
specs weekly (Monday 10:00 UTC) and opens a PR if changed — local specs
are never more than one week stale.

---

## Testing Patterns

- **Framework:** pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **Async tests:** use `@pytest.mark.asyncio()` (with parens), return `-> None`
- **HTTP mocking:** `respx` for httpx calls — use `@respx.mock` decorator
- **App testing:** `TestClient` (sync) or `AsyncClient` via `ASGITransport`

### Fixture dependency graph

```
tmp_data_dir          (temp directory, no deps)
  ├── db              (init SQLite, depends on tmp_data_dir)
  └── test_settings   (AppSettings + auth state reset, depends on tmp_data_dir)
        ├── app       (TestClient, depends on test_settings)
        └── async_client (AsyncClient, depends on test_settings)
```

`db` and `test_settings` are **siblings** — both depend on `tmp_data_dir`
independently. Tests that need a database AND the app must request both
`db` and `app` (or use fixtures that depend on `db`).

### FK constraint pattern

Tests touching `cooldowns` or `search_log` must seed the `instances` table
first via the `seeded_instances` fixture (defined locally in
`tests/test_engine/test_search_loop.py`, `tests/test_engine/test_golden_search_log.py`,
`tests/test_engine/test_supervisor.py`, and `tests/test_services/test_cooldown.py`):

```python
@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
            " VALUES (?, ?, ?, ?, ?)",
            [(1, "Sonarr Test", "sonarr", "http://sonarr:8989", _ENC_KEY),
             (2, "Radarr Test", "radarr", "http://radarr:7878", _ENC_KEY)],
        )
        await conn.commit()
    yield
```

Engine tests set `encrypted_api_key` to a valid Fernet-encrypted value
(`_ENC_KEY`). The simpler 4-column form (without `encrypted_api_key`)
is used in `test_cooldown.py` where only FK constraints matter.

### Login helper for route tests

A `_login()` helper is defined locally in each route test file that needs it
(`test_logs.py`, `test_settings.py`, `test_status.py`):

```python
def _login(client: TestClient) -> None:
    client.post("/setup", data={"username": "admin", "password": "ValidPass1!", ...})
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})
```

### CSRF helper for route tests

Mutating authenticated routes require a valid CSRF token. Use the helpers from
`tests/conftest.py`:

```python
from tests.conftest import csrf_headers, get_csrf_token

resp = client.post("/settings/instances", data=form, headers=csrf_headers(client))
resp = client.delete("/settings/instances/1", headers=csrf_headers(client))
```

Current CSRF exemptions: `POST /logout`, `/login`, `/setup`.

The `test_settings` fixture resets `_auth._serializer` and
`_auth._login_attempts` so auth state does not bleed between tests.

---

## Git & GitHub Workflow

### Issue-first (required)

Every PR must link a pre-existing issue (`Closes #N`). Create the issue
first, then the branch and PR.

**Issue title convention:**
`type: short imperative description` (lowercase, no period)

Examples:
- `fix: application INFO logs missing from stdout`
- `feat: add persistent shell navigation`
- `chore: bump version to 1.0.4`

**Issue label policy — every issue must have:**
- Exactly one `type:*` label (`type: bug`, `type: feature`, `type: docs`,
  `type: chore`, `type: test`, `type: ci`, `type: security`)
- Exactly one `priority:*` label (`priority: high`, `priority: medium`,
  `priority: low`)
- At most one `phase:*` label (for roadmap work only)

Issue templates auto-apply `type:` and `priority: medium` labels.

### Branch naming

`type/short-slug` from `main`:

```
feat/multi-format-copy     fix/clipboard-http-fallback
chore/bump-1.0.4           ci/release-validation
docs/trust-security
```

### Commits

Conventional Commits format: `type(scope): description`

Allowed types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`.

### Pull requests

- **Squash-merge only.** Linear history is enforced by branch protection.
  All three merge strategies are enabled in repo settings, but only squash-merge
  preserves the required linear history.
- All 10 required CI checks must pass before merge.
- Use the PR template: fill in `Closes #N`, check the checklist.
- Branches auto-delete on merge (`deleteBranchOnMerge: true`).

> **Observed practice note:** Issues consistently carry `type:*` and
> `priority:*` labels, but PRs have no labels applied. The PR template
> checklist verifies that the *linked issue* has labels, not the PR itself.

### Restrictions on `main`

- No direct pushes (branch protection + enforce admins)
- No force pushes
- No branch deletion
- All changes go through PRs with passing required checks

---

## Versioning, Changelog & Releases

### Source of truth

`VERSION` and `CHANGELOG.md` are the single source of truth. Everything else
(GitHub Releases, Docker tags, GHCR `latest`) is derived automatically.

- `VERSION` — one line, plain `X.Y.Z` (no `v` prefix)
- `CHANGELOG.md` — Keep a Changelog format

### Release workflow

```
1. Fix/feature PR merged to main
2. Open a separate "chore: bump version to X.Y.Z" PR
   - Change only VERSION and CHANGELOG.md
   - No other files
3. Merge the version bump PR
4. Tag and push:  git tag vX.Y.Z && git push origin vX.Y.Z
   → docker.yml  builds + pushes to GHCR as vX.Y.Z + latest
   → release.yml extracts CHANGELOG block, creates GitHub Release
```

Never push a `v*` tag without a matching `## [X.Y.Z]` block in `CHANGELOG.md`.

### CHANGELOG entry rules

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Fixed

- One sentence. User-facing impact first. Issue/PR ref at end (#N).

### Added

- One sentence per bullet. (#N)

### Changed

- One sentence per bullet. (#N)

### Removed

- One sentence per bullet. (#N)

---
```

**Allowed `###` headers:** `Added`, `Fixed`, `Changed`, `Removed` only.
Level-4 `####` subheadings may group items within `###` sections for major
releases.

**Bullet rules:**
- One sentence per bullet — no multi-line prose
- Lead with user-facing impact, not implementation details
- End with `(#N)` issue/PR reference
- Use backticks for identifiers, file names, env vars, UI elements
- Be specific: `Connection errors now log at WARNING with instance name`
  not `Improved error handling`

**Separators:** Each version block ends with a `---` line (blank line before
and after). Do not use `## [Unreleased]`.

### CI-enforced validation

1. **PR-time** (`version-check.yml`): Runs on PRs touching `VERSION` or
   `CHANGELOG.md`. Validates format, heading match, valid `###` headers,
   `---` separator.
2. **Tag-time** (`release.yml`): Validates VERSION == tag, extracts release
   notes via `awk`, creates GitHub Release using `--notes-file` (avoids
   backtick shell substitution).

---

## Agent Operating Rules

### Scope discipline

1. Investigate and define a tight scope before editing code.
2. Create a GitHub issue first with clear acceptance criteria.
3. Apply mandatory labels on the issue before starting work.
4. Create a scoped branch (`type/short-slug`) from `main`.
5. Implement only issue-scoped changes — avoid mixed concerns.
6. Run all five quality gates before committing.
7. Open a scoped PR linking the issue (`Closes #N`).
8. Merge only after all required checks pass.

### What not to change casually

- `VERSION` and `CHANGELOG.md` — only in dedicated version bump PRs
- `pyproject.toml` tool config (ruff rules, mypy strictness, pytest settings)
- `.github/workflows/` — changes trigger workflow-lint and may affect required checks
- `src/houndarr/database.py` schema migrations — requires `SCHEMA_VERSION` bump
- `tests/conftest.py` shared fixtures — changes affect all test files
- `requirements.txt` / `requirements-dev.txt` — dependency changes require
  `pip-audit` to pass

### When to add or update tests

- Every behaviour change needs a corresponding test change
- New routes need auth, CSRF, and happy-path tests at minimum
- New service functions need unit tests covering success, error, and edge cases
- If fixing a bug, add a regression test that fails without the fix

### When to stop and ask

- Ambiguous requirements or conflicting documentation
- Changes that would affect the release workflow or CI required checks
- Schema migrations or database changes
- Scope creep beyond the linked issue
- Security-sensitive changes (auth, crypto, SSRF validation)

### Avoiding CI/release breakage

- Do not modify the 10 required check job names — branch protection depends
  on exact name matches
- Do not add `## [Unreleased]` to CHANGELOG.md — the release workflow
  extracts content between `## [X.Y.Z]` headings
- Do not change `ci-skip.yml` job names without updating branch protection
- If mypy CI fails with "merge ref not found": push an empty commit to retrigger
- Keep `paths-ignore` patterns in sync across the five main workflows

### Handling conflicts between docs and practice

When documented guidance and observed practice differ, follow the safer rule.
Currently known minor discrepancies:

- Repo settings allow merge commits and rebase merges, but linear history
  protection effectively requires squash-merge. **Always squash-merge.**
- AGENTS.md previously listed `PLW0603` as a suppressed rule, but the `PLW`
  rule family is not selected in the ruff config. The `# noqa: PLW0603`
  comments in source are defensive/inert. **Leave them in place but do not
  rely on `PLW` rules being enforced.**

---

## Public-Facing Voice

All text posted to GitHub under the maintainer's account must read as if a
human wrote it. Agents ghostwrite — they do not narrate, report, or
self-identify.

### Prohibited in all GitHub-visible text

This applies to issue titles, issue bodies, PR titles, PR bodies, PR/issue
comments, commit messages, CHANGELOG entries, and release notes.

Never include:

- References to AGENTS.md, CLAUDE.md, or any instruction file
  (`"I have read AGENTS.md"`, `"per AGENTS.md"`, `"scope discipline"`)
- Agent compliance declarations
  (`"this change is within scope"`, `"I verified"`, `"I audited"`)
- Audit/verification narration
  (`"truth audit"`, `"verified TRUE"`, `"confirmed against the codebase"`,
  `"cross-reference audit"`, `"code-grounded"`, `"release-readiness
  verification"`)
- Finding-ID numbering schemes (`SEC-1`, `F-1`, `FINDING-1`)
- Process theater
  (`"post-fix verification"`, `"remediation plan"`, `"close the remaining
  gaps"`, `"completed housekeeping"`, `"this task is now complete"`)
- Exhaustive negative-finding enumerations (listing every file where
  something was NOT found)
- Quality-gate recitation with exact tool names and test counts
  (`"All 5 quality gates pass: ruff check, ruff format, mypy strict,
  bandit SAST, pytest (312 tests)"` — just say `"all checks pass"`)
- grep/search verification as proof (`"grep -ri returns zero matches"`)
- Post-merge instruction lists in PR bodies
- `"Follow-up recommendations (not in this PR)"` sections
- Item-count narration (`"9 Q&A entries covering every misconception"`)
- Prompt-shaped headings (`"Success criteria"`, `"Evidence"`, `"Decision"`)
- Layer-by-layer audit tables in issue bodies

### Required voice

- Write as the maintainer would: concise, direct, technical.
- Issue bodies: state the problem and what needs to change. A few sentences
  for routine issues, more detail for complex ones.
- PR bodies: say what changed and why. Use the PR template. Do not add
  custom compliance checklists beyond the template.
- PR template checklist: only check items that actually apply. Leave
  inapplicable items unchecked or mark `N/A`.
- Comments: short and human (`"Done"`, `"Fixed in abc1234"`, `"Merged"`).
- Commit messages: follow Conventional Commits. Body optional — if present,
  explain why, not what the agent did.
- CHANGELOG: follow existing bullet rules (already defined above).

### Internal-only text

References to agents, prompts, instruction files, and workflow mechanics
belong only in:

- `AGENTS.md` itself
- `.claude/` or `.cursor/` directories
- Git-ignored local files

They must never appear in any GitHub-visible artifact.

### Documentation voice

All user-facing documentation (website pages, README, CONTRIBUTING, SECURITY,
in-app help text) must read as if a single human maintainer wrote it — direct,
concise, and conversational. Documentation should feel authored, not assembled.

**Prohibited in documentation:**

- `"Mental model"` as a framing device or callout label
- Defensive credibility claims about the document itself
  (`"every claim is based on the source code"`,
  `"where limitations exist, they are stated plainly"`,
  `"these are honest trade-offs"`)
- Summary sections that restate the page's content bullet-by-bullet
- Worked examples that read like textbook exercises (step-by-step arithmetic
  with bold emphasis on each subtraction)
- Exhaustive enumeration of things that are absent (listing many specific
  analytics services that are not used — just say "no analytics or error
  tracking")
- FAQ questions that feel reverse-engineered from a prompt rather than
  sourced from real user confusion
- The same concept explained in near-identical phrasing on more than two pages

**Cross-page repetition rule:**

Each concept (e.g. "skips are normal", "monitored does not mean wanted",
"conservative defaults are slow by design") should have ONE authoritative
explanation on one page. Other pages that mention the concept should use a
brief statement (one sentence) and link to the authority page. Never repeat
the same reassurance formula verbatim across pages.

**Reassurance discipline:**

Avoid repeating reassurance phrases (`"this is expected"`,
`"this does not mean Houndarr is stuck"`, `"a high skip count is healthy"`)
more than once across the entire documentation set. State the fact once,
clearly, and trust the reader.

**FAQ rules:**

- Keep answers to 2–4 sentences. Link to concept pages for detail.
- Do not re-explain the full search funnel in every FAQ entry.
- Write questions in the voice of a real user, not as preemptive
  corrections of anticipated misconceptions.

**Required voice:**

- Write as a maintainer explaining their own tool to a peer.
- Be concise. Prefer short paragraphs and direct statements.
- Vary phrasing across pages — do not use the same sentence structure
  to explain similar concepts.
- Use callouts and admonitions sparingly.
- Headings should be descriptive or action-oriented, not reassuring
  (`"Check the error count"` not `"Zero errors is a strong health signal"`).

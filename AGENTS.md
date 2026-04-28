# AGENTS.md: Houndarr

Cross-tool agent reference for the Houndarr repository.
This file is the primary source of truth for autonomous agents operating here.

## Project Overview

Houndarr is a self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and
Whisparr that automatically searches for missing, cutoff-unmet, and
upgrade-eligible media in small, rate-limited batches. It runs as a single Docker container alongside
an existing *arr stack.

**Tech stack:** Python 3.12 / FastAPI / aiosqlite (SQLite) / Jinja2 / HTMX /
Tailwind CSS CDN. Published to GHCR at `ghcr.io/av1155/houndarr`.

**Scope guard:** Houndarr is a single-purpose tool. Every change must help
search for missing, cutoff-unmet, or upgrade-eligible media in a controlled,
polite way.
Do not add download-client integration, indexer management, request workflows,
multi-user support, or media file manipulation.

---

## Setup & Run

`just` is the canonical interface. Install it via `brew install just`
(macOS) or `cargo install just`. The repo's `justfile` wires every
gate, every test slice, and the dev server, so most agent work goes
through `just <recipe>` rather than `.venv/bin/...`.

```bash
# Create venv and install (one-time bootstrap)
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

# Run locally (dev mode; auto-reload, API docs at /docs)
just dev
```

Dev server: `http://localhost:8877`.

---

## Quality Gates

Run before every commit. CI enforces the same five plus security
and container checks.

```bash
just check      # all gates, CI order: lint + fmt-check + type + sec + test
just quick      # fast loop: lint + type + non-integration pytest
just fix        # ruff --fix + ruff format
just lint | fmt-check | type | sec | test  # individual recipes
```

If `just` is unavailable, read `justfile` for the underlying
`.venv/bin/...` invocations.

---

## Running Tests

~2580 tests (parametrised expansions + 12 async engine-cycle cases
tagged `@pytest.mark.integration`).  `just test`, `test-quick`,
`test-integration`, and `pin` run with `pytest -n auto` by default
(pytest-xdist).  Override with `PYTEST_WORKERS=0` for serial
triage, or `PYTEST_WORKERS=4` to constrain.

```bash
just test               # full suite, parallel
just test-quick         # unit only (-m "not integration"), parallel
just test-integration   # tests/test_e2e/ (-m integration), parallel
just pin                # characterisation tests only, parallel
just test-browser chromium     # Playwright e2e; serial (shared stack on fixed ports)
```

For one-off invocations without a `just` recipe:

```bash
.venv/bin/pytest tests/test_auth.py                                    # single file
.venv/bin/pytest tests/test_auth.py::test_check_password_valid -v      # single test
.venv/bin/pytest -k "csrf" -v                                          # keyword filter
.venv/bin/pytest --cov=houndarr --cov-report=term-missing              # coverage
```

### Markers

- `@pytest.mark.integration` — 12 async engine-cycle cases in
  `tests/test_e2e/` plus 15 Playwright flows in `tests/e2e_browser/`
  (browser tree excluded from default collection via `norecursedirs`;
  `test_e2e/` is collected and filterable).
- `@pytest.mark.pinning` — characterisation tests pinning current
  behaviour before a refactor batch.  Unit-scope; runs in the default
  suite.  Add one whenever a refactor needs a behavioural lock.

Pytest config (`pyproject.toml`): `asyncio_mode = "auto"`,
`asyncio_default_fixture_loop_scope = "function"`,
`addopts = "-q --tb=short"`.

---

## CI Checks

### Required checks (11; branch protection enforced)

| Check name | Workflow file | What it runs |
|------------|---------------|--------------|
| Lint (ruff) | `quality.yml` | `ruff check .` |
| Format (ruff) | `quality.yml` | `ruff format --check .` |
| Type check (mypy) | `quality.yml` | `mypy src/` |
| Test (Python 3.12) | `tests.yml` | `pytest -q --tb=short` + compile check + `--help` |
| Dependency audit (pip-audit) | `security.yml` | `pip-audit -r requirements.txt -r requirements-dev.txt` |
| SAST (bandit) | `security.yml` | `bandit -r src/ -c pyproject.toml` |
| Trivy filesystem scan | `security.yml` | `trivy fs .` (CRITICAL/HIGH with known fix) |
| Dependency review | `dependency-review.yml` | PR dependency diff vs GitHub Advisory Database |
| Build (no push) | `docker.yml` | Multi-arch Docker build (amd64/arm64), no push |
| Trivy image scan | `docker.yml` | Trivy scan of built Docker image (CRITICAL/HIGH with known fix) |
| Security smoke test | `security-smoke-test.yml` | Live container: unauthenticated sweep, CSRF, XFF, rate limiting, API key exposure, container security |

The six main workflows (`quality`, `tests`, `security`, `dependency-review`,
`docker`, `security-smoke-test`) use `paths-ignore: ["docs/**", "**/*.md", "website/**", ".claude/**"]`. When a PR
touches only those paths, `ci-skip.yml` provides passing no-op jobs with
identical check names so branch protection is satisfied.

### Additional workflows (not required checks)

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `version-check.yml` | PRs changing `VERSION` or `CHANGELOG.md` | Validates VERSION format, CHANGELOG heading match, allowed `###` headers, `---` separator |
| `release.yml` | `v*` tag push | Validates VERSION == tag, extracts CHANGELOG block, creates GitHub Release |
| `chart.yml` | `v*` tag push | Packages `charts/houndarr/` with version from `VERSION` file, pushes to `oci://ghcr.io/av1155/charts` |
| `dockerfile-lint.yml` | Changes to `Dockerfile` | `hadolint Dockerfile` |
| `workflow-lint.yml` | Changes to `.github/workflows/**` | `actionlint` via reviewdog |
| `api-snapshot-refresh.yml` | Weekly (Monday 10:00 UTC) + manual | Fetches upstream Radarr/Sonarr/Whisparr/Lidarr/Readarr OpenAPI specs, updates `docs/api/` snapshots and `tests/test_docs_api.py` hashes, opens a PR if changed |
| `pages.yml` | Pushes to `main` touching `website/**` | Deploys docs site to GitHub Pages |
| `test-deploy.yml` | PRs touching `website/**` | Tests Docusaurus build without deploying |
| `link-check.yml` | PRs touching `**/*.md`, `**/*.mdx`, `lychee.toml` + weekly (Monday 08:00 UTC) + manual | Runs `lychee` against every Markdown file to catch broken external links; rules live in `lychee.toml` |
| `cleanup-actions-cache.yml` | Daily (05:00 UTC) + manual | Prunes stale GitHub Actions caches |

### Branch protection on `main`

- 11 required status checks (strict; branch must be up to date)
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
- **Linter/formatter:** Ruff; selected rule sets: `E W F I B C4 UP SIM ANN S N`

### Punctuation

Never use em dashes (`—`) anywhere in source code, comments, HTML templates,
or documentation. Replace with a colon, semicolon, comma, period, or
parentheses depending on the context.

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

- **mypy strict mode**: all public functions need full signatures
- Modern union syntax: `str | None`, not `Optional[str]`
- Builtin generics: `list[str]`, `dict[str, Any]`, not `List`/`Dict`
- `collections.abc.AsyncGenerator`, not `typing.AsyncGenerator`
- Specific error codes: `# type: ignore[assignment]`; never bare `# type: ignore`
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
| Type aliases | PascalCase or Literal | `RunNowStatus = Literal["accepted", "not_found", "disabled"]` |

### Docstrings

- Module-level docstring on every file that contains code
- Google-style for functions: `Args:`, `Returns:`, `Raises:` sections
- Test functions may use brief single-line docstrings

### Comments

Read [`docs/commenting-standard.md`](docs/commenting-standard.md) at least once
per session before writing or editing code in this repo. It codifies the full
commenting standard (per-language rules for Python, HTML/Jinja2, CSS, JS, SQL,
YAML, shell, Markdown) plus the universal principles that apply across all of
them. Agents and human contributors alike are expected to match what is there;
reviewers will flag comments that do not.

Core rule (full rationale in the standard): **comments explain _why_, code
explains _what_**. If a comment just restates the code, delete it and rename
the variable or function instead.

### Logging

Every module that logs uses `logger = logging.getLogger(__name__)` at module
level. Root logger is configured in `__main__.py` via `logging.basicConfig()`.
No alternative logging libraries (structlog, loguru) are used.

### Error Handling

- **Background tasks:** `except asyncio.CancelledError: raise` first, then
  broad `except Exception` with `# noqa: BLE001`; log + continue/retry
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
| `S608` + `nosec B608` | Dynamic SQL with explicit column allowlist (4 files) |
| `BLE001` | Broad exception in background loops (always with logging) |
| `A002` | Parameter names `type`/`id` shadowing builtins (FastAPI form/function signature convention) |
| `SLF001` | Test fixtures and `__main__.py` accessing private module state |
| `PLW0603` | Module-level global reassignment (singletons); the `PLW` rule family is not currently selected in ruff config, so these comments are defensive/inert |
| `S101` | Defensive assert in adapters and instance validation; also globally ignored in ruff config. Per-file comments are defensive. |

---

## Architecture

### Source layout

```
src/houndarr/
  __main__.py          # CLI entry point (Click), logging setup, uvicorn.run
  app.py               # create_app(), lifespan, middleware registration
  auth/                # AuthMiddleware, bcrypt, CSRF, rate limiter (seam package)
    password.py        # bcrypt verify / hash helpers
    rate_limit.py      # in-memory login rate limiter
    session.py         # signed session cookie encode / decode
    setup.py           # first-run admin setup + password policy
    csrf.py            # CSRF double-submit token rotation
    proxy_auth.py      # reverse-proxy trust gate and header extraction
    identity.py        # current-user resolution from session or proxy header
    middleware.py      # AuthMiddleware dispatch (builtin vs proxy path)
  config.py            # AppSettings dataclass, get_settings() singleton
  crypto.py            # Fernet encrypt/decrypt, master key management
  database.py          # get_db() context manager, schema migrations
  enums.py             # StrEnum consolidation (SearchKind, SearchAction, CycleTrigger, ItemType)
  errors.py            # HoundarrError hierarchy (Client/Engine/Service/Route)
  value_objects.py     # Frozen value objects shared across layers (ItemRef)
  clients/             # httpx-based *arr API clients
    base.py            # ArrClient ABC with _get()/_post() + raise_for_status() + get_queue_status()
    sonarr.py          # SonarrClient (episode/season search, v3 API)
    radarr.py          # RadarrClient (movie search, v3 API)
    lidarr.py          # LidarrClient (album/artist search, v1 API)
    readarr.py         # ReadarrClient (book/author search, v1 API)
    whisparr_v2.py     # WhisparrV2Client (Sonarr-based, episode/season search)
    whisparr_v3.py     # WhisparrV3Client (v3, Radarr-based, movie/scene search)
  engine/
    candidates.py      # SearchCandidate dataclass, ItemType re-export, date helpers
    search_loop.py     # run_instance_search(): unified search pipeline (missing/cutoff/upgrade passes, queue-backpressure gate)
    supervisor.py      # Supervisor: one asyncio.Task per enabled instance
    adapters/
      __init__.py      # AppAdapter dataclass, ADAPTERS registry, get_adapter()
      protocols.py     # AppAdapterProto: runtime_checkable Protocol matching the AppAdapter shape
      sonarr.py        # Sonarr adapter: candidate conversion + dispatch
      radarr.py        # Radarr adapter: candidate conversion + dispatch
      lidarr.py        # Lidarr adapter: candidate conversion + dispatch
      readarr.py       # Readarr adapter: candidate conversion + dispatch
      whisparr_v2.py   # Whisparr v2 adapter: candidate conversion + dispatch
      whisparr_v3.py   # Whisparr v3 adapter: movie/scene candidate conversion + dispatch
  routes/
    _htmx.py           # is_hx_request() shared helper for partial vs full renders
    pages.py           # Setup, Login, Dashboard, Logs, Settings page routes
    health.py          # GET /api/health (Docker HEALTHCHECK)
    settings/          # Settings surface split by concern
      __init__.py      # composes the sub-routers into a single settings_router
      _helpers.py      # template render, client build, connection check, validators
      page.py          # GET /settings
      account.py       # POST /settings/account/password
      instances.py     # /settings/instances/* (CRUD, test-connection, toggle)
    api/
      logs.py          # GET /api/logs (JSON, with cursor-based pagination)
      status.py        # GET /api/status (JSON, dashboard polling)
  services/
    instances.py       # Instance CRUD, InstanceType StrEnum
    cooldown.py        # Per-item search cooldown tracking
    url_validation.py  # SSRF guard for instance URLs
```

### Key patterns

- **Database:** SQLite via aiosqlite; schema version 13; `get_db()` async
  context manager opens a fresh connection per call (FKs enabled per
  connection; WAL mode set once in `init_db()`)
- **Config:** `AppSettings` is a plain dataclass (not Pydantic); `get_settings()`
  is a lazy singleton. Pydantic is used only at the *arr wire boundary
  (`src/houndarr/clients/_wire_models/`), not for internal domain models or
  config
- **Wire models:** every *arr HTTP response is validated with a Pydantic
  model from the `clients/_wire_models/` package before it reaches a parser.
  `PaginatedResponse[T]` (generic, PEP 695 syntax) covers the shared
  `/wanted/*` envelope; `SystemStatus` and `QueueStatus` back
  `ArrClient.ping()` and `ArrClient.get_queue_status()`; per-app
  `*WantedEpisode` / `*WantedMovie` / `*WantedAlbum` / `*WantedBook`
  and `*LibraryEpisode` / `*LibraryMovie` / `*LibraryAlbum` / `*LibraryBook`
  models name the record shapes.  `ArrSeries` / `ArrArtist` / `ArrAuthor`
  type the parent-aggregate fetches.  All wire models extend an internal
  `_ArrModel` that sets `populate_by_name=True` + `extra="ignore"` so
  unknown fields from new *arr versions never raise.  Field names are
  snake_case in Python and alias to the camelCase the APIs serialise.
- **Domain models:** the parsed result types (`MissingEpisode`,
  `LibraryMovie`, etc.) are frozen dataclasses, one per client file
  next to the client that builds them.  Every frozen dataclass in the
  codebase uses `slots=True`.  `Instance` composes seven frozen
  sub-structs (`core`, `missing`, `cutoff`, `upgrade`, `schedule`,
  `snapshot`, `timestamps`) and is itself frozen and slotted; callers
  evolve it through `dataclasses.replace`.  `AppSettings` is the only
  deliberately-mutable dataclass (env overrides applied in-place on
  the lazy singleton).
- **Encryption:** Master key in `request.app.state.master_key`; passed
  explicitly to service functions as `master_key=` kwarg; never imported globally
- **Auth:** Global `AuthMiddleware` (Starlette `BaseHTTPMiddleware`) handles
  session validation and CSRF enforcement; no per-route auth decorators.
  Proxy-auth trust and header reads flow through two primitives in
  `auth.py`: `_is_trusted_proxy(request)` (IP gate) and
  `_extract_proxy_username(request)` (header read, assumes trust
  already verified).  The middleware's `_dispatch_proxy` and the
  standalone `_validate_proxy_auth` both compose these so the gate
  logic lives in one place.
- **HTMX:** SPA-like shell navigation; nav links use `hx-target="#app-content"`
  with `hx-swap="innerHTML"` and `hx-push-url="true"`. Routes check
  `is_hx_request(request)` from `routes/_htmx.py` and return either partial
  or full template.
  Templates are lazily initialised via a module-level singleton
- **Supervisor:** One `asyncio.Task` per enabled instance; 10s shutdown timeout
- **search_log:** Every search attempt writes a row with action
  `searched`/`skipped`/`error`/`info`

### Database schema (SQLite)

| Table | Purpose | Key constraints |
|-------|---------|-----------------|
| `settings` | Key-value config store | `key TEXT PK` |
| `instances` | *arr instance configs | `type CHECK IN ('radarr','sonarr','lidarr','readarr','whisparr_v2','whisparr_v3')`; many policy columns with CHECK constraints; `monitored_total` / `unreleased_count` / `snapshot_refreshed_at` populated by the supervisor's snapshot refresh task |
| `cooldowns` | Per-item search cooldown tracking | `instance_id FK→instances ON DELETE CASCADE`; `UNIQUE(instance_id, item_id, item_type)`; `search_kind CHECK IN ('missing','cutoff','upgrade')` (v15) |
| `search_log` | Audit trail | `instance_id FK→instances ON DELETE SET NULL`; `action CHECK IN ('searched','skipped','error','info')` |

Full DDL and migrations live in `src/houndarr/database.py`. Bump
`SCHEMA_VERSION` and add a `_migrate_to_vN` when changing schema.

### Migration constants are version-locked

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

### *arr API reference (local)

Full upstream OpenAPI specs vendored under `docs/api/` (one per app:
sonarr, radarr, whisparr_v2, whisparr_v3, lidarr, readarr).
**Source of truth** when touching `clients/` code; see
`docs/api/README.md`.  Refreshed weekly (Mon 10:00 UTC) by
`api-snapshot-refresh.yml`, so specs are never more than a week
stale.

---

## Testing Patterns

- **Framework:** pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **Async tests:** use `@pytest.mark.asyncio()` (with parens), return `-> None`
- **HTTP mocking:** `respx` for httpx calls; use `@respx.mock` decorator
- **App testing:** `TestClient` (sync) or `AsyncClient` via `ASGITransport`

### Fixture dependency graph

```
tmp_data_dir          (temp directory, no deps)
  ├── db              (init SQLite, depends on tmp_data_dir)
  └── test_settings   (AppSettings + auth state reset, depends on tmp_data_dir)
        ├── app       (TestClient, depends on test_settings)
        └── async_client (AsyncClient, depends on test_settings)
```

`db` and `test_settings` are **siblings**; both depend on `tmp_data_dir`
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

The `test_settings` fixture resets `_auth._serializer`,
`_auth._setup_complete`, and `_auth._login_attempts` so auth state does
not bleed between tests.

---

## Verifying Claims About Algorithms

Before modifying search-engine logic, scheduling, randomisation, ordering,
distribution, or any code where probability or stateful iteration governs
behaviour, verify the claim empirically and analytically first. Most
reported "bugs" in this class turn out to be sample noise, observation
bias, or misreadings of timing-dependent state, and shipping a fix for a
non-bug introduces real risk for no real gain.

### When this rule fires

Apply this workflow whenever a user, a code review, or another AI surfaces
a claim along the lines of:

- "X picks the wrong page / item / branch"
- "Y is biased / unfair / skewed toward Z"
- "Random does not feel random"
- "The cycle order is broken"
- "We are searching the same things over and over"

It does not apply to clear logic bugs, typos, or behaviour-change
requests. The trigger is specifically: claims about probabilistic or
distribution-shaped behaviour where the right answer is a measured
histogram, not a code reading.

### Required workflow

1. Reproduce the algorithm in isolation against `tests/mock_arr/`, not
   against the live test instances or short-window log dumps. The live
   test *arrs hold tens of records, which is far below the sample size
   needed to distinguish bias from variance, and live state (cooldowns,
   hourly caps, *arr-side sort orders) confounds the measurement.
2. Derive analytically what each page, item, or branch's probability
   should be under the current code. Read the loop, write the math
   down, and predict the distribution shape before running anything.
   "I think it should be uniform" is not a prediction; "uniform with
   chi-square below 16.92 at df=9" is.
3. Run hundreds of cycles through `tests/mock_arr/probe_distribution.py`
   or a similar probe modelled on it. Compute chi-square, max/min
   ratio, and per-bucket standard deviation. Compare against the
   analytical prediction and against the 5% chi-square critical value
   at `df = N - 1`.
4. Decide on evidence. If the empirical result agrees with the
   prediction and the chi-square lands below the critical value, the
   claim is wrong. Document the finding, reference the probe output,
   and close the investigation. If the result confirms real bias,
   scope the fix to the smallest change that closes the measured gap,
   then re-run the probe to prove the gap is gone.

### Tooling to use

- `just mock-arr port=PORT items=N seed=S` launches the seeded
  multi-app mock server with configurable item counts and a
  deterministic seed; identical seeds produce byte-identical responses.
- `.venv/bin/python -m tests.mock_arr.probe_distribution` boots the
  mock in-process, drives the production `run_instance_search` for
  many cycles across a sweep of library sizes, and reports per-cycle
  start-page distributions plus full visit histograms. Use it as the
  template for any new programmatic probe.
- The mock exposes `GET /__page_log__/{app}` and
  `GET /__commands__/{app}` for ground-truth request and dispatch
  records, plus `POST /__reset__/{app}` to clear them between
  configurations.
- For statistical-power-bound questions (100k+ trials), a short
  pure-Python simulation of just the algorithm beats running through
  HTTP. Use it when the measurement is about the math, not the
  integration.

### What not to do

- Do not treat a short-window dev-DB histogram (a few hours, dozens
  of cycles, a handful of items) as evidence of algorithmic bias.
  Cooldown phase, *arr-side sort order, and small-sample variance
  dominate that signal. The math you owe is a many-cycle distribution
  against a predicted shape.
- Do not adopt an external diagnostic write-up without re-deriving
  the math yourself. Direction (page 1 vs page N) and magnitude
  (1.5x vs 5x) routinely invert in second-hand summaries of
  probabilistic algorithms, and shipping a fix for an inverted claim
  ships a regression.
- Do not start coding because the claim is plausible. Plausibility is
  not evidence. The bar is a reproducible measurement that disagrees
  with the predicted distribution by more than chance.

### Closing the loop

When measurement contradicts the claim, the writeup is the engineering
contribution. Reference the probe output, state the measured statistics,
explain what the original observation was actually picking up
(cooldown saturation, recency effects, sort-order interaction, sample
noise), and close the discussion. A correct "no change required" is a
successful task, not a non-result.

### Known emergent behaviours (already measured)

These are real but minor effects that have been verified by probe and
deliberately left alone. Do not re-investigate them unless the
operating point changes or a user reports a concrete regression.

- Partial-last-page over-selection on missing/cutoff under random
  search order. When the engine's `page_size` does not divide
  `totalRecords` evenly, items on the (short) last page are drained
  every visit because the engine dispatches up to `batch_size` items
  per page. Measured at most 2x attention skew for the 1-9 items on
  the last page at default settings (batch=1, pageSize=10) and 4x in
  contrived configurations (batch=5, pageSize=20). Affects a small
  slice of the backlog; the only clean fix is a virtual flat-index
  draw which is a substantial redesign of `_run_search_pass`. Probe:
  `tests/mock_arr/probe_cooldown.py`.
- Sonarr / Whisparr v2 windowed-rotation coverage time. The upgrade
  pass visits 5 series per cycle; full-library coverage takes
  approximately `ceil(eligible_episodes * H / batch)` cycles where H
  is the harmonic-coverage factor. Measured 91% theoretical and
  85-89% empirical coverage at 60 cycles with batch=5 on 50 series.
  This is the intentional trade-off versus hammering one series with
  a single huge *arr fetch. Probe:
  `tests/mock_arr/probe_upgrade_coverage.py`.

---

## Git & GitHub Workflow

### Issue-first (required)

Every PR must link a pre-existing issue (`Closes #N`). If an issue already
exists for the problem being solved (e.g. a user-reported bug), use that
issue. Only create a new issue when one does not already exist.

**Issue title convention:**
`type: short imperative description` (lowercase, no period)

Examples:
- `fix: application INFO logs missing from stdout`
- `feat: add persistent shell navigation`
- `chore: bump version to 1.0.4`

**Issue label policy; every issue must have:**
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

Subject line max 50 characters (including the `type(scope): ` prefix); body lines max 72 characters.

### Pull requests

- **Squash-merge only.** Linear history is enforced by branch protection.
  All three merge strategies are enabled in repo settings, but only squash-merge
  preserves the required linear history.
- All 11 required CI checks must pass before merge.
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
- After each merge, run `git fetch --all --prune --tags` and delete local branches whose upstream is gone (`git branch -vv` shows `[gone]`).

---

## Versioning, Changelog & Releases

### Source of truth

`VERSION` and `CHANGELOG.md` are the single source of truth. Everything else
(GitHub Releases, Docker tags, GHCR `latest`) is derived automatically.

- `VERSION`: one line, plain `X.Y.Z` (no `v` prefix)
- `CHANGELOG.md`: [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
  with a `## [Unreleased]` section at the top and one versioned block per
  release below it

### Release workflow

```
1. Each fix/feature PR adds a bullet under `## [Unreleased]` in
   CHANGELOG.md as part of its own commit (the /ship workflow
   handles this for user-facing changes; non-user-facing PRs skip
   the bullet).
2. When ready to release, open a separate "chore: bump version to
   X.Y.Z" PR via /bump:
   - Promote `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
   - Reseed an empty `## [Unreleased]` block above the new
     versioned block
   - Change only VERSION and CHANGELOG.md (no other files)
3. Merge the version bump PR.
4. Tag and push:  git tag vX.Y.Z && git push origin vX.Y.Z
   → docker.yml  builds + pushes to GHCR as vX.Y.Z + latest
   → release.yml extracts the X.Y.Z CHANGELOG block, creates GitHub Release
   → chart.yml   packages + pushes Helm chart to oci://ghcr.io/av1155/charts
```

Never push a `v*` tag without a matching `## [X.Y.Z] - YYYY-MM-DD` block
in `CHANGELOG.md`.

### Changelog style guide

The audience is self-hosters and homelab operators running Houndarr
in Docker or Kubernetes alongside the *arr stack.  They read config
files, env vars, log lines, and SQLite schemas; they do not read the
Python source.  Tune every bullet for that reader.

**Voice (noun-led, present tense):**

The category heading carries the verb (`### Added`, `### Fixed`,
`### Changed`).  Bullets describe the post-change state from the
reader's vantage point, not the maintainer's action:

- Good: `Logs page distinguishes a fresh install (No log entries yet)
  from a filter that matches nothing (No entries match those filters.). (#566)`
- Avoid: `We added a fresh-install vs filter-empty distinction to the
  logs page.` (narrator voice)
- Avoid: `Distinguish fresh-install from filter-empty on the logs
  page.` (imperative; reserved for SDK changelogs)

This matches the convention used by Authelia, AdGuard Home, Plausible,
Caddy, and other self-hosted tools targeting the same audience.

**Length:**

- Target: 80 to 160 characters per bullet.
- Hard ceiling: 250 characters.  A bullet longer than that must split
  into two unrelated bullets, or the second clause moves to the PR body.
- One sentence per bullet.  A second sentence is permitted only when
  a migration or upgrade-affecting consequence must ride with the
  change (rare).

**Vocabulary the operator can act on, only:**

- Use: env var names (`HOUNDARR_COOKIE_SAMESITE`), config keys, schema
  version numbers (`Schema v16`), database column names that survive
  in the SQLite file (`monitored_total`, `whisparr_episode`), HTTP
  routes (`/api/status`), log strings the operator can grep
  (`hourly limit reached (N/hr)`), CVE IDs, dependency versions when
  a security or behaviour change ties to the bump.
- Avoid: internal Python class names (`InstanceValidationError`,
  `AuthMiddleware._dispatch_proxy`), private helpers
  (`_redirect_guard`, `_run_search_pass`), file paths under `src/`,
  module attribute names that have no user surface.  Describe the
  user-visible behaviour instead.
- Borderline: public library types that surface in tracebacks
  (`httpx.TransportError`).  Allowed when the user actually sees the
  type name in their logs, otherwise paraphrase to "transport-level
  error".

**Banned phrasings (drift signals; rewrite or drop):**

- Vague: "Various bug fixes", "Minor improvements", "Misc updates",
  "Bug fixes and stability improvements".
- Marketing: "We are thrilled to...", "delightful new experience",
  "groundbreaking", "exciting".
- Magic adverbs without measurement: "seamlessly", "robustly",
  "significantly", "dramatically".  Either quantify ("reduces idle
  CPU by 60%") or omit.
- Empty verbs: "leverages", "utilizes", "harnesses", "facilitates".
  Pick the concrete verb.
- Vague comparatives: "Improved error handling", "Enhanced UX",
  "Better performance".  Name the change: "Connection errors now
  log at WARNING with the instance name."
- Bold lead-ins: `**Performance:** faster X`.  Plain bullet.
- Marketing trail clauses: "for a smoother experience".  Stop at the
  technical fact.
- Past-tense narration: "We added...", "We fixed...".  Drop the
  pronoun.
- Em dashes anywhere (project-wide rule; use a colon, semicolon,
  comma, period, or parentheses).

**What does NOT belong in the changelog:**

- Pure refactors with no user-visible behaviour change (Common
  Changelog explicitly excludes these; they live in PR bodies).
- Test-only changes.
- Docs-only changes (the docs site has its own deploy log).
- CI / workflow changes that do not affect deployers.
- Dependency bumps with no security or behaviour impact.

**Schema version bumps:**

When a release ships a SQLite schema migration, name the schema
number, what the migration touches, and any rollback constraint.  An
AdGuard-Home-style "to roll back, downgrade to <previous tag>" line
helps operators who restore from a backup.

**Examples (verbatim from the repo, judged):**

- Exemplary: ``Helm chart `appVersion` is now prefixed with `v` so it
  matches the published Docker image tags. (#364)`` (102 chars; named
  user-visible attribute; one causal clause).
- Exemplary: ``Hourly rate-limit skip rows now read `hourly limit
  reached (N/hr)` across missing, cutoff, and upgrade passes. (#491)``
  (names the exact log string the operator greps for).
- Over-technical (rewrite before merging): ``Curated
  `InstanceValidationError.public_message` text replaces the raw
  exception in instance validation banners`` should read ``Instance
  validation banner shows a curated message instead of the raw Python
  exception``.
- Over-technical (rewrite): ``Random search order now uses a
  stratified-shuffle page deck plus partial-page sentinel padding``
  should read ``Random search order spreads dispatch probability
  uniformly across the backlog so no page is over- or under-selected``.

### CHANGELOG entry rules

CHANGELOG.md always carries a `## [Unreleased]` section above every
versioned block:

```markdown
## [Unreleased]

### Added

- One sentence per bullet. (#N)

### Fixed

- One sentence. User-facing impact first. Issue/PR ref at end (#N).

---

## [X.Y.Z] - YYYY-MM-DD

### Added

- One sentence per bullet. (#N)

### Changed

- One sentence per bullet. (#N)

### Fixed

- One sentence per bullet. (#N)

### Removed

- One sentence per bullet. (#N)

---
```

**Allowed `###` headers (Keep a Changelog 1.1.0):** `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed`, `Security`. Level-4 `####` subheadings
may group items within `###` sections for major releases. Omit any
section that has no entries.

**Bullet rules:**
- Add the bullet to `## [Unreleased]` as part of the same PR that
  ships the change. /bump promotes the accumulated Unreleased block
  to a versioned heading at release time.
- Every bullet must be justified by a PR-body sentence, a diff fragment,
  or a source `file:line`. Do not draft from PR titles, commit messages,
  or memory alone. The verification protocol lives in
  `.claude/commands/bump.md` §3b; skipping it is what shipped the
  inaccurate v1.9.0 bullets that had to be corrected in #420.
- Adopt the PR author's vocabulary for nuance. If the PR body says
  "new default for fresh installs; existing instances keep their prior
  behaviour," the bullet says "new default for newly added instances,"
  not "new default."
- One sentence per bullet; no multi-line prose.
- Lead with user-facing impact, not implementation details.
- End with `(#N)` issue/PR reference.
- Use backticks for identifiers, file names, env vars, UI elements.
- Use markdown `[text](url)` syntax for links; bare URLs do not auto-link
  in the in-app `What's New` modal (GitHub's CHANGELOG view autolinks both,
  but the modal's `_render_changelog_bullet` filter only accepts the
  `[text](url)` form).
- Be specific: `Connection errors now log at WARNING with instance name`
  not `Improved error handling`.

**Separators:** Both `## [Unreleased]` and every versioned block end with
a `---` line (blank line before and after). The fresh Unreleased block
reseeded by /bump carries only the heading and the trailing `---`.

**Non-user-facing PRs:** CI-only, refactor-only, test-only, docs-only,
and chore/infrastructure changes do not get a Changelog bullet. The
/ship workflow filters these out automatically.

### CI-enforced validation

1. **PR-time** (`version-check.yml`): Runs on PRs touching `VERSION` or
   `CHANGELOG.md`. Validates VERSION format, requires `## [Unreleased]`
   as the topmost `## [...]` block, validates the Unreleased block's
   `###` headers + trailing `---` separator, and validates the `## [VERSION] - YYYY-MM-DD`
   block matches VERSION with valid `###` headers + trailing `---`.
2. **Tag-time** (`release.yml`): Validates VERSION == tag, extracts the
   `## [X.Y.Z]` block via `awk`, creates GitHub Release using
   `--notes-file` (avoids backtick shell substitution).

The in-app `What's New` modal parser (`src/houndarr/services/changelog.py`)
silently skips `## [Unreleased]` because the heading lacks the `X.Y.Z`
plus ISO-date suffix that `_VERSION_HEADING` requires; users only see
versioned blocks until /bump promotes Unreleased.

---

## Agent Operating Rules

### Scope discipline

1. Investigate and define a tight scope before editing code.
2. Link an existing issue, or create one if none exists.
3. Apply mandatory labels on the issue before starting work.
4. Create a scoped branch (`type/short-slug`) from `main`.
5. Implement only issue-scoped changes; avoid mixed concerns.
6. Run all five quality gates before committing.
7. Open a scoped PR linking the issue (`Closes #N`).
8. Merge only after all required checks pass.

### Issue triage labels

When replying to an issue with a question or a request for more information
(logs, reproduction steps, curl output, etc.), add the `waiting-for-reporter`
label. A daily workflow (`stale.yml`) marks these issues stale after 4 days
and closes them after 3 more days of silence. The `unstale.yml` companion
workflow automatically removes both `stale` and `waiting-for-reporter` when
someone comments, so reporters get immediate feedback.

### What not to change casually

- `VERSION` and `CHANGELOG.md`: only in dedicated version bump PRs
- `pyproject.toml` tool config (ruff rules, mypy strictness, pytest settings)
- `.github/workflows/`: changes trigger workflow-lint and may affect required checks
- `src/houndarr/database.py` schema migrations: requires `SCHEMA_VERSION` bump
- `tests/conftest.py` shared fixtures: changes affect all test files
- `requirements.txt` / `requirements-dev.txt`: dependency changes require
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

- Do not modify the 11 required check job names; branch protection depends
  on exact name matches
- Do not delete the `## [Unreleased]` block at the top of CHANGELOG.md;
  `version-check.yml` requires it as the topmost `## [...]` block on every
  PR, and `/bump` reseeds an empty one after each promotion
- Do not change `ci-skip.yml` job names without updating branch protection
- If mypy CI fails with "merge ref not found": push an empty commit to retrigger
- Keep `paths-ignore` patterns in sync across the six main workflows

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
human wrote it. Agents ghostwrite; they do not narrate, report, or
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
  bandit SAST, pytest (312 tests)"`: just say `"all checks pass"`)
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
- Commit messages: follow Conventional Commits. Body optional; if present,
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
in-app help text) must read as if a single human maintainer wrote it: direct,
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
  analytics services that are not used; just say "no analytics or error
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
- Vary phrasing across pages; do not use the same sentence structure
  to explain similar concepts.
- Use callouts and admonitions sparingly.
- Headings should be descriptive or action-oriented, not reassuring
  (`"Check the error count"` not `"Zero errors is a strong health signal"`).

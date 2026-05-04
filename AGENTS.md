# AGENTS.md: Houndarr

Cross-tool agent reference for the Houndarr repository.
This file is the primary source of truth for autonomous agents operating here.

## Project Overview

Houndarr is a self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and
Whisparr that automatically searches for missing, cutoff-unmet, and
upgrade-eligible media in small, rate-limited batches. It runs as a single Docker container alongside
an existing *arr stack.

**Tech stack:** Python 3.13 / FastAPI / aiosqlite (SQLite) / Jinja2 / HTMX /
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
# Create venv and install (one-time bootstrap; uv reads pyproject.toml +
# uv.lock and installs both runtime and the PEP 735 `dev` group by default).
uv sync

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

~2580 tests. `just test`, `test-quick`, `test-integration`, and `pin`
run with `pytest -n auto` (pytest-xdist). Override with
`PYTEST_WORKERS=0` for serial triage, or `PYTEST_WORKERS=4` to constrain.

```bash
just test                       # full suite
just test-quick                 # unit only (-m "not integration")
just test-integration           # async engine cycles
just pin                        # characterisation tests
just test-browser chromium      # Playwright e2e (serial, shared stack)
.venv/bin/pytest tests/test_auth.py::test_x -v   # one-off
```

Markers: `@pytest.mark.integration` (12 async engine-cycle cases in
`tests/test_e2e/` + 15 Playwright flows in `tests/e2e_browser/`; browser
tree excluded from default collection via `norecursedirs`),
`@pytest.mark.pinning` (characterisation tests, runs in default suite).
Fixture graph, FK-seeding pattern, and route-test helpers live in the
`houndarr-testing` skill (loads on `tests/**`).

---

## CI Checks

11 required status checks (branch protection enforced). Full check
table, additional non-required workflows, and the paths-ignore /
ci-skip pattern live in the `houndarr-ci` skill (loads on
`.github/workflows/**`):

| Check group | Workflow |
|-------------|----------|
| Lint / Format / Type check (ruff, mypy) | `quality.yml` |
| Test (Python 3.13) | `tests.yml` |
| Dependency audit (pip-audit), SAST (bandit), Trivy fs | `security.yml` |
| Dependency review | `dependency-review.yml` |
| Build (no push), Trivy image scan | `docker.yml` |
| Security smoke test | `security-smoke-test.yml` |

The six main workflows use
`paths-ignore: ["docs/**", "**/*.md", "website/**", ".claude/**"]`;
`ci-skip.yml` provides passing no-op jobs with identical check names so
docs-only PRs satisfy branch protection. **Do not modify the 11 required
check job names**: branch protection depends on exact name matches.

Branch protection on `main`: 11 required status checks (strict; branch
must be up to date), required PR reviews, linear history, no force
pushes, no branch deletions, enforce admins, CODEOWNERS `@av1155`.

---

## Code Style

### Formatting

- **Line length:** 100 characters
- **Indentation:** 4 spaces (2 for YAML / JSON / TOML)
- **Target Python:** 3.13+ (`target-version = "py313"` in `pyproject.toml`)
- **Linter / formatter:** Ruff; rule sets `E W F I B C4 UP SIM ANN S N`
- **Type check:** mypy strict mode

### Punctuation (project-wide)

Never use em dashes (`—`) anywhere in source code, comments, HTML
templates, or documentation. Replace with a colon, semicolon, comma,
period, or parentheses depending on the context.

### Comments

Read [`docs/commenting-standard.md`](docs/commenting-standard.md) at
least once per session before writing or editing code in this repo. It
codifies the full commenting standard (per-language rules for Python,
HTML/Jinja2, CSS, JS, SQL, YAML, shell, Markdown) plus the universal
principles. Core rule: **comments explain _why_, code explains _what_**.

### Python conventions

Generic Python conventions (PEP 604 unions, lowercase generics, naming,
mypy strict, etc.) live in the global `python` skill (auto-loads on
`.py` edits). Houndarr-specific additions, the `noqa` / `nosec`
suppression table, the AppSettings-as-plain-dataclass decision, the
frozen-dataclass-with-slots invariant for domain models, the per-module
logger pattern, and the background-task error-handling shape live in
the `houndarr-python` skill (loads on `**/*.py`).

---

## Architecture

Detailed source layout, wire-models vs domain-models split, auth
composition, HTMX shell pattern, and *arr API spec snapshots live in
the `houndarr-architecture` skill (loads on `src/houndarr/**`).
Database schema and migration discipline live in the
`houndarr-database` skill (loads on `src/houndarr/database.py`).
Algorithmic verification protocol lives in the `verify-algorithms`
skill (loads on `src/houndarr/engine/**`).

### Non-obvious patterns to know up front

- **Database**: SQLite via aiosqlite, schema version 13. `get_db()`
  opens a fresh connection per call (FKs enabled per connection; WAL
  mode set once in `init_db()`).
- **Config**: `AppSettings` is a plain dataclass, **not Pydantic**.
  Pydantic is used only at the *arr wire boundary
  (`src/houndarr/clients/_wire_models/`).
- **Domain models**: frozen dataclasses with `slots=True`. `Instance`
  composes seven frozen sub-structs and is itself frozen and slotted;
  callers evolve through `dataclasses.replace`. `AppSettings` is the
  only deliberately-mutable dataclass.
- **Encryption**: master key in `request.app.state.master_key`; passed
  explicitly as `master_key=` kwarg; never imported globally.
- **Auth**: global `AuthMiddleware` (Starlette `BaseHTTPMiddleware`)
  handles session validation and CSRF; no per-route auth decorators.
  Proxy-auth gate logic lives in `_is_trusted_proxy` +
  `_extract_proxy_username` so dispatch and validation share one code
  path.
- **Supervisor**: one `asyncio.Task` per enabled instance; 10s shutdown
  timeout.
- **search_log**: every search attempt writes a row with action
  `searched` / `skipped` / `error` / `info`.
- **HTMX**: SPA-shell navigation; nav links use
  `hx-target="#app-content"` with `hx-swap="innerHTML"` and
  `hx-push-url="true"`. Routes check `is_hx_request(request)` from
  `routes/_htmx.py` and return partial vs full template accordingly.

---

## Testing Patterns

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **HTTP mocking**: `respx` for httpx calls (`@respx.mock`)
- **App testing**: `TestClient` (sync) or `AsyncClient` via `ASGITransport`

Fixture dependency graph, FK-seeding pattern for `cooldowns` /
`search_log`, login / CSRF helpers, and the `test_settings` auth-state
reset all live in the `houndarr-testing` skill (loads on `tests/**`).

---

## Verifying Claims About Algorithms

Before modifying search-engine logic, scheduling, randomisation,
ordering, distribution, or any code where probability or stateful
iteration governs behaviour, verify the claim empirically and
analytically first. Most reported "bugs" in this class turn out to be
sample noise, observation bias, or misreadings of timing-dependent
state, and shipping a fix for a non-bug introduces real risk for no
real gain.

Full workflow (when the rule fires, the four required steps, the
mock-arr / probe tooling, anti-patterns, and the list of already-measured
emergent behaviours that should not be re-investigated) lives in the
`verify-algorithms` skill (loads on `src/houndarr/engine/**`).

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

### Changelog rules

Voice (noun-led, present tense), length targets, vocabulary do's and
don'ts, banned phrasings, the bullet-justification protocol that
prevents drafting from PR titles or memory, separator rules, allowed
`###` headers, and CI-enforced validation all live in the
`houndarr-changelog` skill (loads on `CHANGELOG.md`, `VERSION`).

Project-wide reminder: changelog bullets are written for self-hoster
operators (env vars, log lines, schema versions), not for Python
contributors (class names, private helpers, file paths under `src/`).

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
- `pyproject.toml` (`[project] dependencies` + `[dependency-groups] dev`)
  and `uv.lock`: dependency changes require `pip-audit` to pass.
  `requirements.txt` / `requirements-dev.txt` are no longer checked in;
  the security workflow generates them on the fly via `uv export`

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

## Agent skills

Per-repo configuration consumed by skills like `to-issues`, `triage`,
`to-prd`, `qa`, `improve-codebase-architecture`, `diagnose`, and
`grill-with-docs`. Edit the files under `docs/agents/` directly; rerun
`/setup-matt-pocock-skills` only to switch issue trackers or restart from
scratch.

### Issue tracker

GitHub issues on `av1155/houndarr`, accessed via the `gh` CLI. See
`docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles map to repo labels: `needs-info` reuses the
existing `waiting-for-reporter` (which drives the stale automation),
`wontfix` is unchanged, and `needs-triage` / `ready-for-agent` /
`ready-for-human` keep their canonical names and need creating before
first use. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout. `CONTEXT.md` and `docs/adr/` live at the repo
root if and when `/grill-with-docs` produces them. See
`docs/agents/domain.md`.

---

## Public-Facing Voice

All text posted to GitHub under the maintainer's account must read as
if a human wrote it. Agents ghostwrite; they do not narrate, report,
or self-identify.

### Prohibited in all GitHub-visible text

Applies to issue titles, issue bodies, PR titles, PR bodies, PR / issue
comments, commit messages, CHANGELOG entries, and release notes. Never
include:

- References to AGENTS.md, CLAUDE.md, or any instruction file
  (`"per AGENTS.md"`, `"scope discipline"`).
- Agent compliance declarations (`"this change is within scope"`,
  `"I verified"`, `"I audited"`).
- Audit / verification narration (`"truth audit"`, `"verified TRUE"`,
  `"confirmed against the codebase"`, `"code-grounded"`).
- Finding-ID numbering schemes (`SEC-1`, `F-1`, `FINDING-1`).
- Process theater (`"post-fix verification"`, `"remediation plan"`,
  `"completed housekeeping"`, `"this task is now complete"`).
- Exhaustive negative-finding enumerations (every file where something
  was NOT found).
- Quality-gate recitation with exact tool names and test counts: just
  say `"all checks pass"`.
- grep / search verification as proof (`"grep -ri returns zero matches"`).
- Post-merge instruction lists in PR bodies, or
  `"Follow-up recommendations (not in this PR)"` sections.
- Item-count narration (`"9 Q&A entries covering every misconception"`).
- Prompt-shaped headings (`"Success criteria"`, `"Evidence"`,
  `"Decision"`).
- Layer-by-layer audit tables in issue bodies.

### Required voice

- Write as the maintainer would: concise, direct, technical.
- Issue bodies: state the problem and what needs to change. A few
  sentences for routine issues, more detail for complex ones.
- PR bodies: say what changed and why. Use the PR template. Do not add
  custom compliance checklists beyond the template. Only check items
  that actually apply; leave inapplicable items unchecked or mark `N/A`.
- Comments: short and human (`"Done"`, `"Fixed in abc1234"`, `"Merged"`).
- Commit messages: follow Conventional Commits. Body optional; if
  present, explain why, not what the agent did.

### Internal-only text

References to agents, prompts, instruction files, and workflow mechanics
belong only in `AGENTS.md`, `.claude/`, or git-ignored local files.
They must never appear in any GitHub-visible artifact.

### Documentation voice (docs site, README, in-app help)

User-facing documentation should read as if a single human maintainer
wrote it: direct, concise, conversational, **authored not assembled**.

- Avoid `"Mental model"` framings, defensive credibility claims about
  the document itself, summary sections that restate the page, textbook
  worked examples, and exhaustive enumerations of things that are absent.
- One authoritative explanation per concept; other pages link to it
  rather than repeat the wording. State each fact once and trust the
  reader; do not stack reassurance phrases (`"this is expected"`,
  `"a high skip count is healthy"`).
- FAQ answers: 2-4 sentences. Phrase questions in real-user voice, not
  as preemptive corrections of anticipated misconceptions.
- Headings should be descriptive or action-oriented, not reassuring
  (`"Check the error count"` not `"Zero errors is a strong health
  signal"`).

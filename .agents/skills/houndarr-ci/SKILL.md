---
name: houndarr-ci
description: Houndarr's CI workflow reference and branch protection. Loads when reading or editing .github/workflows/. Covers the 12 required status checks (exact names matter for branch protection), the additional non-required workflows, the docs-only classifier that skips required jobs on docs-only PRs, and the rule against changing required check job names.
paths:
  - ".github/workflows/**"
---

# Houndarr CI conventions

## Required checks (12; branch protection enforced)

| Check name | Workflow file | What it runs |
|------------|---------------|--------------|
| Lint (ruff) | `quality.yml` | `ruff check .` |
| Format (ruff) | `quality.yml` | `ruff format --check .` |
| Type check (mypy) | `quality.yml` | `mypy src/` |
| Test (Python 3.13) | `tests.yml` | `pytest -q --tb=short` + compile check + `--help` |
| Test (Python 3.14) | `tests.yml` | The same job on 3.14, the interpreter the Docker image ships |
| Dependency audit (pip-audit) | `security.yml` | `pip-audit` against `requirements.txt` + `requirements-dev.txt` + `requirements-e2e.txt` generated on the fly via `uv export --frozen` from `pyproject.toml` + `uv.lock` |
| SAST (bandit) | `security.yml` | `bandit -r src/ -c pyproject.toml` |
| Trivy filesystem scan | `security.yml` | `trivy fs .` (CRITICAL/HIGH with known fix) |
| Dependency review | `dependency-review.yml` | PR dependency diff vs GitHub Advisory Database |
| Build (no push) | `docker.yml` | Docker build, no push: amd64 on PRs, amd64 + arm64 on `chore/bump-*` PRs |
| Trivy image scan | `docker.yml` | Trivy scan of built Docker image (CRITICAL/HIGH with known fix) |
| Security smoke test | `security-smoke-test.yml` | Live container: unauthenticated sweep, CSRF, XFF, rate limiting, API key exposure, container security |

`quality` and `tests` run in full on every PR: ruff checks every `.py`
and `.md` file, and the tests read `docs/api/`, `scripts/marketing/` and
`.claude/settings.json`. The other four main workflows (`security`,
`dependency-review`, `docker`, `security-smoke-test`) also run on every
PR. Each has a `changes` job that calls `docs-only.yml` (check name
`Docs-only check / Classify changed files`), and their required jobs are
skipped by `if:` when it reports `docs_only == 'true'`. Branch protection
counts a job skipped that way as passing. `Build (no push)` is the
exception: it runs and skips each of its steps, because its name is an
expression.

The classifier says `true` only when the PR targets the default branch,
its head has not moved, the file list is complete and not empty (under
3000 entries and at least `changed_files` distinct paths), and every
path, both sides of a rename included, matches `docs/**`, `**/*.md`,
`website/**`, `.claude/**` or `scripts/marketing/**`. A failed or
skipped classifier (any non-PR event) never skips a job. It needs only
`pull-requests: read`, which fork PR tokens have.
`tests/test_gates/test_docs_only_gate.py` runs its script under node and
pins the job conditions.

## Additional workflows (not required checks)

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `docs-only.yml` | `workflow_call` from the four workflows above | Classifies a PR as docs-only for its callers; no concurrency block, since `github.workflow` there is the caller's |
| `browser-e2e.yml` | PRs (paths-ignore equal to `DOCS_ONLY_GLOBS`) + manual | Builds the image, starts mock Sonarr/Radarr, and runs `tests/e2e_browser/` on chromium, firefox and webkit with the locked `e2e` group (`uv sync --frozen --group e2e`) |
| `version-check.yml` | PRs changing `VERSION` or `CHANGELOG.md` | Validates VERSION format, CHANGELOG heading match, allowed `###` headers, `---` separator |
| `release.yml` | `v*` tag push | Validates VERSION == tag, extracts CHANGELOG block, creates GitHub Release |
| `chart.yml` | `v*` tag push | Packages `charts/houndarr/` with version from `VERSION` file, pushes to `oci://ghcr.io/av1155/charts` |
| `dockerfile-lint.yml` | Changes to `Dockerfile` | `hadolint Dockerfile` |
| `workflow-lint.yml` | Changes to `.github/workflows/**` | `actionlint` via reviewdog, plus a pin check that fails on any non-local action not pinned to a full commit SHA with a trailing comment |
| `api-snapshot-refresh.yml` | Weekly (Monday 10:00 UTC) + manual | Fetches upstream Radarr/Sonarr/Whisparr/Lidarr/Readarr OpenAPI specs, updates `docs/api/` snapshots and `tests/test_docs_api.py` hashes, opens a PR if changed |
| `pages.yml` | Pushes to `main` touching `website/**` | Deploys docs site to GitHub Pages |
| `test-deploy.yml` | PRs touching `website/**` or the workflow itself | Tests the Docusaurus build without deploying; its `Trivy website scan` job fails on fixable CRITICAL / HIGH findings in the docs site's dependencies |
| `link-check.yml` | PRs touching `**/*.md`, `**/*.mdx`, `lychee.toml` + weekly (Monday 08:00 UTC) + manual | Runs `lychee` against every Markdown file to catch broken external links; rules live in `lychee.toml` |
| `cleanup-actions-cache.yml` | Weekly (Monday 05:00 UTC) + manual | Prunes stale GitHub Actions caches |

## Branch protection on `main`

- 12 required status checks (strict; branch must be up to date)
- PRs required, stale reviews dismissed, conversation resolution required
- Required approving reviews is 0, so no approval blocks a merge
- Linear history enforced (no merge commits)
- No force pushes, no branch deletions
- Admin enforcement is off. The rules above bind contributors; the
  maintainer account can push to `main` and merge past a failing check.
  The hooks in `.claude/settings.json` are the real block on direct
  edits and pushes while on `main`.
- CODEOWNERS assigns `@av1155` to every file, but code owner review is
  not a required gate

## Don't break this

- Do not modify the 12 required check job names; branch protection
  depends on exact name matches.
- Do not put a `paths` or `paths-ignore` filter on a workflow with a
  required check: a workflow skipped by a path filter leaves its checks
  pending. `browser-e2e.yml` keeps its filter because its check is not
  required; keep its list equal to `DOCS_ONLY_GLOBS` in `docs-only.yml`.
- Do not skip a job whose name is an expression with a job-level `if:`:
  it reports under the unevaluated expression and its required check
  stays pending. Gate its steps, as `docker.yml`'s build does with
  `DOCS_ONLY`, and gate any step added there too.
- Gate a required job as
  `!cancelled() && !(needs.changes.result == 'success' && needs.changes.outputs.docs_only == 'true')`,
  so only a successful classifier can skip it.
- Each caller's `changes` job grants `docs-only.yml` its permissions:
  widening the classifier's means widening all four callers in the same
  PR.
- If mypy CI fails with "merge ref not found": push an empty commit to
  retrigger.
- Keep every action pinned to a full 40-character commit SHA with the
  release version as the last token of a trailing comment
  (`uses: owner/repo@<sha> # vX.Y.Z`). Dependabot rewrites the SHA and
  the comment together, but only while the version ends the comment.
  A tag or branch ref can be repointed by the action owner; a SHA
  cannot. When pinning by hand, dereference annotated tags to their
  commit: for those actions `git/ref/tags/vN` returns a tag object
  whose SHA the runner cannot resolve.
- Every Trivy job pins `version:` on its first trivy-action call, and
  later calls in the job reuse that binary and DB with
  `skip-setup-trivy: "true"` and `cache: "false"`. Dependabot bumps the
  action but not this input, so raise it by hand. Keep it at 0.72 or
  later: earlier releases read only the first document of a
  multi-document pnpm lockfile and pass with nothing scanned. pnpm
  writes one once a project has config dependencies, declares
  `devEngines.packageManager`, or pins pnpm 12.
- No Trivy ignore file exists today. `.trivyignore.yaml` paths match
  relative to the scan root, so suppressing a website finding takes two
  new files. A root `.trivyignore.yaml` lists `website/pnpm-lock.yaml`
  for `security.yml`, which scans `.`; a bare `pnpm-lock.yaml` there
  would match the root CSS-build lockfile instead. A
  `website/.trivyignore.yaml` lists `pnpm-lock.yaml` for the
  `Trivy website scan` job, which scans `website/`, and its path keeps
  that job triggering when the file changes. Wire each one in with
  `trivyignores:` on the calls that scan with it: both `security.yml`
  steps and the website job's gating step.

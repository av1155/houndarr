---
name: houndarr-ci
description: Houndarr's CI workflow reference and branch protection. Loads when reading or editing .github/workflows/. Covers the 11 required status checks (exact names matter for branch protection), the additional non-required workflows, the paths-ignore + ci-skip pattern that keeps docs-only PRs green, and the rule against changing required check job names.
paths:
  - ".github/workflows/**"
---

# Houndarr CI conventions

## Required checks (11; branch protection enforced)

| Check name | Workflow file | What it runs |
|------------|---------------|--------------|
| Lint (ruff) | `quality.yml` | `ruff check .` |
| Format (ruff) | `quality.yml` | `ruff format --check .` |
| Type check (mypy) | `quality.yml` | `mypy src/` |
| Test (Python 3.13) | `tests.yml` | `pytest -q --tb=short` + compile check + `--help` |
| Dependency audit (pip-audit) | `security.yml` | `pip-audit` against `requirements.txt` + `requirements-dev.txt` generated on the fly via `uv export --frozen` from `pyproject.toml` + `uv.lock` |
| SAST (bandit) | `security.yml` | `bandit -r src/ -c pyproject.toml` |
| Trivy filesystem scan | `security.yml` | `trivy fs .` (CRITICAL/HIGH with known fix) |
| Dependency review | `dependency-review.yml` | PR dependency diff vs GitHub Advisory Database |
| Build (no push) | `docker.yml` | Multi-arch Docker build (amd64/arm64), no push |
| Trivy image scan | `docker.yml` | Trivy scan of built Docker image (CRITICAL/HIGH with known fix) |
| Security smoke test | `security-smoke-test.yml` | Live container: unauthenticated sweep, CSRF, XFF, rate limiting, API key exposure, container security |

The six main workflows (`quality`, `tests`, `security`, `dependency-review`,
`docker`, `security-smoke-test`) use
`paths-ignore: ["docs/**", "**/*.md", "website/**", ".claude/**"]`. When a PR
touches only those paths, `ci-skip.yml` provides passing no-op jobs with
identical check names so branch protection is satisfied.

## Additional workflows (not required checks)

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

## Branch protection on `main`

- 11 required status checks (strict; branch must be up to date)
- Required PR reviews enabled (dismiss stale reviews, required conversation resolution)
- Linear history enforced (no merge commits)
- No force pushes, no branch deletions
- Enforce admins enabled
- CODEOWNERS: `@av1155` owns all files

## Don't break this

- Do not modify the 11 required check job names; branch protection
  depends on exact name matches.
- Do not change `ci-skip.yml` job names without updating branch protection.
- Keep `paths-ignore` patterns in sync across the six main workflows.
- If mypy CI fails with "merge ref not found": push an empty commit to
  retrigger.

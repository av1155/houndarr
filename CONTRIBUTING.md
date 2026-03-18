# Contributing to Houndarr

Thanks for your interest in improving Houndarr.

## Scope

Houndarr is a focused tool for controlled *arr search automation (Radarr, Sonarr, Lidarr, Readarr, Whisparr).
Please keep changes aligned with the project goal and avoid scope expansion.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e .
```

## Workflow

1. Create a GitHub issue first. Use the Conventional Commits `type:` prefix in the
   title (`type: short imperative description`, lowercase, no period — e.g.
   `fix: login fails on empty password`). Apply exactly one `type:*` label and one
   `priority:*` label before starting work.
2. Create a short-lived branch from `main` (for example `feat/<slug>` or `fix/<slug>`).
3. Implement your change with tests.
4. Run all quality gates locally.
5. Commit using Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, etc.).
6. Push and open a PR that links the issue (`Closes #<number>` when appropriate).
7. Wait for all CI checks to pass before merge.

For releases, open a separate `chore: bump version to X.Y.Z` PR that changes only
`VERSION` and `CHANGELOG.md` together — CI will validate they match.

## Required local checks

```bash
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
.venv/bin/pytest
```

## Pull request guidance

- Keep PRs focused and small.
- Update tests for behavior changes.
- Avoid committing secrets, local DB files, or generated artifacts.
- For UI changes, include screenshots in the PR description.

## Code style

- Python target is `>=3.12`.
- Follow existing project patterns and naming.
- Prefer minimal, maintainable changes over broad rewrites.

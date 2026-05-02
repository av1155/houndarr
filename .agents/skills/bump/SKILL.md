---
name: bump
description: Bump Houndarr version and prepare a release PR. Promotes the Unreleased CHANGELOG block to a versioned block, runs quality gates, opens the PR. Use when the user asks to bump the version, prepare a release, or run /bump. Trigger phrases include bump, release, version bump, prepare release.
---

# Bump Version and Release

Prepare a release PR following the Houndarr versioning workflow.

Houndarr follows Keep a Changelog 1.1.0: every shipped PR adds a bullet under `## [Unreleased]` as part of its own commit, so by the time bump runs, the upcoming release's bullets are already in the file. This skill's job is to **promote** the Unreleased block to a versioned block, not to draft one from PR titles.

The user's argument is the version bump kind: `patch`, `minor`, or an explicit `X.Y.Z`.

## 1. Read Current Version

```
cat VERSION
```

Note the current version (plain X.Y.Z, no `v` prefix).

## 2. Calculate New Version

- `patch`: increment patch (e.g. 1.6.5 → 1.6.6)
- `minor`: increment minor, reset patch
- Explicit `X.Y.Z`: use as-is

Validate the new version is greater than the current one.

## 3. Verify the Unreleased Block

### 3a. Refuse to bump an empty Unreleased

```
awk '/^## \[Unreleased\]$/{found=1; next} found && /^## \[/{exit} found{print}' CHANGELOG.md
```

If the extracted block contains nothing more than the trailing `---`, stop with `"## [Unreleased] is empty. Nothing to release."` and do not touch VERSION or CHANGELOG.

### 3b. Verify every bullet's claim before promoting

**Mandatory.** For every PR referenced in the Unreleased block:

```bash
gh pr view N --repo <owner>/<repo> --json title,body
gh pr diff N --repo <owner>/<repo> | head -400
```

For any bullet that claims behavior (a default, a UI element, an error string, an API response shape), read the relevant source file and pin the claim to a specific `file:line`. Adopt the PR author's vocabulary for nuance. Every bullet must be defensible with a concrete reference; if you cannot cite one, rewrite or drop the bullet.

### 3c. Final filter and language pass

Keep only user-facing entries (Added, Changed, Fixed, Removed, Deprecated, Security). Drop CI/refactor/test-only/docs-only/chore/dep-bump-without-impact entries.

If no user-facing entries remain, report `"No user-facing changes in Unreleased. Nothing to release."` and stop.

Language pass: rewrite bullets that reference internal Python classes / private helpers / `src/...` paths, exceed 250 chars without a migration reason, use banned phrasings (various / improved / enhanced / better / seamlessly / robustly / significantly / leverages / utilizes / em dashes), or read as past-tense narration. Use markdown `[text](url)` for links.

## 4. Present the Promotion Plan for Review

Show the planned CHANGELOG.md unified diff. Wait for approval. Do not proceed without confirmation.

## 5. Create Branch and Tracking Issue

```
git fetch origin
git checkout -b chore/bump-X.Y.Z origin/main
gh issue create --title "chore: bump version to X.Y.Z" \
  --label "type: chore" --label "priority: medium" \
  --body "Release X.Y.Z with <brief summary of user-facing changes>."
```

## 6. Update VERSION File

Write the new version (single line, no `v` prefix).

## 7. Promote Unreleased in CHANGELOG.md

Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and insert a fresh empty Unreleased block at the top.

## 8. Run Quality Gates

```
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
```

## 9. Commit and Push

```
git add VERSION CHANGELOG.md
git commit -m "chore: bump version to X.Y.Z"
git push -u origin HEAD
```

## 10. Create PR

```
gh pr create --title "chore: bump version to X.Y.Z" --body "..."
```

After merge, remind the user to run `git tag vX.Y.Z && git push origin vX.Y.Z` to trigger docker.yml, release.yml, and chart.yml.

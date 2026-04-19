---
description: Bump version and prepare a release PR
argument-hint: "<patch|minor|X.Y.Z>"
allowed-tools: Read, Write, Edit, Bash(git *), Bash(gh *), Bash(date *), Bash(.venv/bin/python *), Bash(.venv/bin/pytest *)
---

# Bump Version and Release

Prepare a release PR following the Houndarr versioning workflow.

## 1. Read Current Version

```
cat VERSION
```

Note the current version (plain X.Y.Z, no `v` prefix).

## 2. Calculate New Version

Parse `$ARGUMENTS`:

- `patch`: increment the patch number (e.g. 1.6.5 → 1.6.6)
- `minor`: increment the minor number, reset patch (e.g. 1.6.5 → 1.7.0)
- Explicit `X.Y.Z`: use as-is

Validate the new version is greater than the current one.

## 3. Draft CHANGELOG Entries

Read the git log since the last tag:

```
git log $(git describe --tags --abbrev=0)..HEAD --oneline
```

From these commits, build CHANGELOG entries following the exact rules
from AGENTS.md (Versioning, Changelog & Releases section):

**Include only user-facing changes:**
- Features (Added)
- Bug fixes (Fixed)
- Behavioral changes, UI changes, config changes (Changed)
- Removed functionality (Removed)

**Exclude:**
- CI changes, refactors, test-only changes, docs-only changes
- Chore/infrastructure work that does not affect the user

If after filtering there are NO user-facing changes, report
"No user-facing changes since vX.Y.Z. Nothing to release." and stop.
Do not create a branch, do not touch VERSION or CHANGELOG.

**Format:**

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

**Rules:**
- Allowed `###` headers: `Added`, `Fixed`, `Changed`, `Removed` only.
  Omit any section that has no entries.
- One sentence per bullet; no multi-line prose.
- Lead with user-facing impact, not implementation details.
- End with `(#N)` issue/PR reference.
- Use backticks for identifiers, file names, env vars, UI elements.
- Use markdown `[text](url)` syntax for links; bare URLs do not auto-link
  in the in-app `What's New` modal (the modal's `_render_changelog_bullet`
  filter only accepts the `[text](url)` form). GitHub's CHANGELOG view
  autolinks either form, so markdown links work in both places.
- Be specific: `Connection errors now log at WARNING with instance name`
  not `Improved error handling`.
- Each version block ends with a `---` line (blank line before and after).
- Do not use `## [Unreleased]`.

## 4. Present Draft for Review

Show me the draft CHANGELOG block before writing it. Wait for my
approval or edits. Do not proceed until I confirm.

## 5. Create Branch and Tracking Issue

```
git fetch origin
git checkout -b chore/bump-X.Y.Z origin/main
```

Create a tracking issue (every PR must link one):

```
gh issue create --title "chore: bump version to X.Y.Z" \
  --label "type: chore" --label "priority: medium" \
  --body "Release X.Y.Z with <brief summary of user-facing changes>."
```

## 6. Update VERSION File

Write the new version (single line, no `v` prefix, no trailing newline
beyond what was already there).

## 7. Write CHANGELOG.md

Insert the new version block at the top of CHANGELOG.md, directly
after any header content and before the previous version block.

## 8. Run Quality Gates

```
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
```

No pytest needed (only VERSION and CHANGELOG.md changed).

## 9. Commit and Push

```
git add VERSION CHANGELOG.md
git commit -m "chore: bump version to X.Y.Z"
git push -u origin HEAD
```

Only VERSION and CHANGELOG.md should be in this commit. No other files.

## 10. Create PR

Title must match: `chore: bump version to X.Y.Z`

```
gh pr create --title "chore: bump version to X.Y.Z" --body "$(cat <<'PREOF'
Closes #N

## What changed

Version bump to X.Y.Z. Only VERSION and CHANGELOG.md are modified.

## Release notes (from CHANGELOG)

<paste the CHANGELOG entries here>

## Checklist

- [x] Linked issue has `type:*` and `priority:*` labels
- [x] Only VERSION and CHANGELOG.md changed
- [x] CHANGELOG entry follows existing format
PREOF
)"
```

After merge, the release workflow is:

```
git tag vX.Y.Z && git push origin vX.Y.Z
```

This triggers docker.yml (GHCR push), release.yml (GitHub Release),
and chart.yml (Helm chart push). Do NOT push the tag automatically;
remind me to do it after merge.

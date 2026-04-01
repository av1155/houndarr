---
description: End-to-end workflow for fixing a GitHub issue
argument-hint: "<issue-number>"
allowed-tools: Read, Write, Edit, MultiEdit, Bash(*), Grep, Glob
---

# Fix GitHub Issue

Work through a GitHub issue from start to finish.

## 1. Read the Issue

```
gh issue view $ARGUMENTS --json title,body,labels,assignees
```

Understand the problem. Note the issue title prefix (fix:, feat:, etc.)
to determine the branch type.

## 2. Verify Labels

Confirm the issue has:
- Exactly one `type:*` label
- Exactly one `priority:*` label

If labels are missing, add them:

```
gh issue edit $ARGUMENTS --add-label "type: bug,priority: medium"
```

## 3. Create Branch

Determine the branch type from the issue title or labels.
Create a scoped branch from latest main:

```
git fetch origin
git checkout -b type/short-slug origin/main
```

Use the type from the issue title (fix/, feat/, chore/, docs/, etc.)
and a short descriptive slug.

## 4. Investigate

Read the relevant source files to understand the problem.
Trace the code path from entry point to the issue location.
Check for existing tests covering the affected area.

## 5. Implement

Make the minimum change needed. Follow the conventions in AGENTS.md:
- `from __future__ import annotations` as first line
- Type annotations on all public functions
- Google-style docstrings
- Module-level `logger = logging.getLogger(__name__)`

## 6. Add or Update Tests

- Bug fix: add a regression test that fails without the fix
- New route: auth, CSRF, and happy-path tests at minimum
- New service function: success, error, and edge case tests

## 7. Run Quality Gates

Run all five gates and fix any failures:

```
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
.venv/bin/pytest
```

## 8. Commit

Use Conventional Commits format. Reference the issue:

```
git add <specific files>
git commit -m "type(scope): description

Closes #$ARGUMENTS"
```

Subject line max 50 characters. Body wrapped at 72.

## 9. Push and Create PR

```
git push -u origin HEAD
```

Then create the PR using the repo's template structure:

```
gh pr create --title "type(scope): description" --body "$(cat <<'EOF'
## Summary

Brief description of what changed and why.

Closes #$ARGUMENTS

## Changes

- First change
- Second change

## Testing

All checks pass.

## Type of Change

- [x] Bug fix (`fix:`)

## Checklist

- [x] Issue exists and is linked above
- [x] Linked issue has type:* and priority:* labels
- [x] Branch name matches issue scope
- [x] Tests added/updated
- [x] Type check passes
- [x] Lint passes
- [x] Format passes
- [x] No secrets committed
- [x] Scope check: helps search for missing or cutoff-unmet media
EOF
)"
```

Adjust the type of change and checklist items to match the actual work.

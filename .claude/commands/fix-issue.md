---
description: Turn any input into a shipped PR
argument-hint: "<issue-number | URL | description>"
allowed-tools: Read, Write, Edit, MultiEdit, Bash(*), Grep, Glob
---

# Fix: Any Input → Shipped PR

Take any input (issue, URL, alert, plain text) and produce a
disciplined PR. Adaptive about input, strict about output.

## 1. Parse Input

Detect what `$ARGUMENTS` is:

- **GitHub issue number** (e.g. `322`):
  ```
  gh issue view $ARGUMENTS --json title,body,labels,assignees,url
  ```

- **GitHub issue URL** (contains `/issues/`):
  Extract the number, then `gh issue view`.

- **GitHub PR URL** (contains `/pull/`):
  Read the PR as a problem description, not as code to merge.
  ```
  gh pr view <number> --json title,body,url
  ```

- **GitHub discussion URL** (contains `/discussions/`):
  ```
  gh api repos/{owner}/{repo}/discussions/<number>
  ```

- **CodeQL / Dependabot / security alert URL**:
  Parse the alert type and number from the URL.
  ```
  gh api repos/{owner}/{repo}/code-scanning/alerts/<number>
  gh api repos/{owner}/{repo}/dependabot/alerts/<number>
  ```

- **Plain text description**: use as-is.

## 2. Summarize and Confirm Scope

Present a concise summary of the problem:
- What is broken or missing
- Where the likely code paths are
- Proposed scope of the fix

Wait for my confirmation before proceeding. Do not start implementation
until I approve the scope.

## 3. Check for Tracking Issue

If the input was NOT a GitHub issue (discussion, PR, alert, or plain
text), ask whether to:

- Create a new tracking issue, or
- Proceed without one

If creating an issue, use the standard format:
```
gh issue create --title "type: description" \
  --body "..." \
  --label "type: bug" --label "priority: medium"
```

## 4. Create Branch

```
git fetch origin
git checkout -b type/short-slug origin/main
```

Determine the branch type from the problem:
- Bug fix → `fix/`
- New feature → `feat/`
- Security alert → `fix/` or `security/`
- Dependency update → `chore/`

## 5. Investigate

Read the relevant source files. Trace the code path from entry point
to the affected area. Check for existing tests covering the behavior.

## 6. Implement

Make the minimum change needed. Follow AGENTS.md conventions:
- `from __future__ import annotations` as first line
- Type annotations on all public functions
- Google-style docstrings
- Module-level `logger = logging.getLogger(__name__)`

## 7. Add or Update Tests

- Bug fix: regression test that fails without the fix
- New route: auth, CSRF, and happy-path tests at minimum
- New service function: success, error, and edge case tests
- Security fix: test that the vulnerability is no longer exploitable

## 8. Run Quality Gates

```
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
.venv/bin/pytest
```

Fix any failures before proceeding.

## 9. Commit

Use Conventional Commits format. Reference the source:

```
git add <specific files>
git commit -m "type(scope): description

Closes #N"
```

If the input was a PR, discussion, or alert (not an issue), reference
it in the commit body: `Ref: <URL>`.

Subject line max 50 characters. Body wrapped at 72.

## 10. Push and Create PR

```
git push -u origin HEAD
```

Create the PR linking the original source:

```
gh pr create --title "type(scope): description" --body "$(cat <<'EOF'
## Summary

Brief description of what changed and why.

Closes #N

## Changes

- First change
- Second change

## Testing

All checks pass.

## Type of Change

- [ ] Bug fix (`fix:`)
- [ ] New feature (`feat:`)
- [ ] Refactoring (`refactor:`)
- [ ] Documentation (`docs:`)
- [ ] CI/CD (`ci:`)
- [ ] Chore (`chore:`)

## Checklist

- [ ] Issue exists and is linked above
- [ ] Linked issue has type:* and priority:* labels
- [ ] Branch name matches issue scope
- [ ] Tests added/updated
- [ ] Type check passes
- [ ] Lint passes
- [ ] Format passes
- [ ] No secrets committed
- [ ] Scope check: helps search for missing or cutoff-unmet media
EOF
)"
```

Check the applicable items. Leave inapplicable items unchecked.

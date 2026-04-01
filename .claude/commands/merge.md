---
description: Ship changes end-to-end (issue, branch, commit, PR, CI, merge, cleanup)
allowed-tools: Bash(git *), Bash(gh *), Bash(sleep *), Bash(.venv/bin/python *)
---

# Ship Changes

Fully automatic end-to-end workflow. Detects the current state and
picks up from wherever the process left off. No confirmation prompts
unless something fails or is ambiguous.

## 0. Detect State

Run these to understand where we are:

```
git branch --show-current
git status --short
git diff --cached --name-only
git log --oneline -5
```

Also check for an existing PR on the current branch:

```
gh pr list --state open --head "$(git branch --show-current)" \
  --json number,title,url,statusCheckRollup --limit 1
```

Use the results to decide which steps to skip:

- Already on a feature branch? Skip step 2.
- No uncommitted changes and commits already ahead of main? Skip step 3.
- PR already exists? Skip step 5.
- CI already passing? Skip step 6.
- Already merged? Skip to cleanup.

If there are no changes (working tree clean, nothing staged, no
commits ahead of main), stop and report "Nothing to ship."

## 1. Issue (if none exists for this work)

Every PR must link a pre-existing issue. If one already exists (user
provided it, or there is a matching open issue), use it. Otherwise
create one:

```
gh issue create --title "<type>: <short imperative description>" \
  --label "type: <type>" --label "priority: medium" \
  --body "<brief description>"
```

Issue title convention: `type: short imperative description`
(lowercase, no period). Examples:
- `fix: application INFO logs missing from stdout`
- `feat: add persistent shell navigation`
- `chore: bump version to 1.0.4`

Required labels on every issue:
- Exactly one `type:*` label
- Exactly one `priority:*` label
- At most one `phase:*` label (for roadmap work only)

## 2. Create Branch (if on main)

```
git fetch origin
git checkout -b <type>/<short-slug> origin/main
```

Branch naming: `type/short-slug` from main. Short, lowercase,
hyphenated. Derive the type from the nature of the changes (feat, fix,
chore, ci, docs, refactor, test).

## 3. Stage and Commit (if uncommitted changes exist)

Run all five quality gates first:

```
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
.venv/bin/python -m pytest -q --tb=short
```

If any gate fails, stop and report the failure. Do not continue.

If only non-code files changed (markdown, yaml, config), skip quality
gates that do not apply.

Stage the relevant files (prefer explicit paths over `git add -A`).
Write a conventional commit message: `type(scope): description`

Commit rules:
- Subject line max 50 characters (including `type(scope): ` prefix)
- Body lines max 72 characters
- If there are multiple logical changes, create separate commits

## 4. Push

```
git push -u origin HEAD
```

## 5. Create PR (if none exists)

Use the PR template at `.github/pull_request_template.md` as the
structure for the body. Fill in each section, replace HTML comments
with actual content, link the issue with `Closes #N`, check the
correct Type of Change box, and check only the Checklist items that
actually apply.

```
gh pr create --title "<short title under 50 chars>" --body "<filled-in template>"
```

## 6. Wait for CI

```
gh pr checks
```

If checks are still running, poll every 30 seconds until all pass or
one fails:

```
while true; do
  STATUS=$(gh pr checks --json name,state --jq '[.[] | select(.state != "SUCCESS" and .state != "SKIPPED")] | length')
  FAILED=$(gh pr checks --json name,state --jq '[.[] | select(.state == "FAILURE")] | length')
  if [ "$FAILED" -gt 0 ]; then
    echo "One or more checks failed:"
    gh pr checks --json name,state --jq '.[] | select(.state == "FAILURE") | "\(.name): \(.state)"'
    break
  fi
  if [ "$STATUS" -eq 0 ]; then
    echo "All checks passed."
    break
  fi
  PENDING=$(gh pr checks --json name,state --jq '[.[] | select(.state == "PENDING" or .state == "QUEUED" or .state == "IN_PROGRESS")] | length')
  echo "$PENDING checks still running... (polling in 30s)"
  sleep 30
done
```

If any check fails, stop and report which ones. Do not merge.

## 7. Merge

Squash-merge only (linear history enforced by branch protection):

```
gh pr merge --squash --delete-branch
```

If the PR requires review approval and does not have it, report that
and stop.

## 8. Post-Merge Cleanup

```
git checkout main
git pull origin main
git fetch --all --prune --tags
```

Delete the local feature branch if it still exists:

```
git branch -d <branch-name>
```

Check for any other local branches whose upstream is gone:

```
git branch -vv | grep '[gone]'
```

If any show `[gone]`, delete them too.

Verify the remote branch was auto-deleted:

```
git branch -r | grep <branch-name>
```

If the remote branch still exists, delete it:

```
gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/<branch-name>
```

## 9. Report

Summarize:
- What was shipped (PR number and title)
- Issue linked or created (number)
- New HEAD commit on main (hash and message)
- Local and remote branches cleaned up (or any that remain)

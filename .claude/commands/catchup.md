---
description: Restore context after /clear or new session
allowed-tools: Read, Bash(git *), Bash(gh *), Bash(date *)
---

# Catch Up on Current Work

## 1. Orientation

Run these commands (always, regardless of branch):

```
git branch --show-current
git status --short
git log --oneline -10
```

This gives: current branch, uncommitted changes, and recent commits.

## 2. Open PRs

If on a feature branch, check for an open PR on this branch:

```
gh pr list --state open --head "$(git branch --show-current)" \
  --json number,title,url,statusCheckRollup --limit 1
```

If on main, check for any open PRs:

```
gh pr list --state open --limit 5 \
  --json number,title,headRefName
```

## 3. Changed Files

If on a feature branch (not main):

```
git diff --name-only main...HEAD
```

Read every file listed in the diff output. This shows what the
current branch has changed relative to main.

If on main: skip this step. Recent context comes from git log
(step 1).

## 4. Summarize

Report:

- Current branch name
- Uncommitted changes (if any)
- Open PR for current branch (number, title, check status)
- What has been done (from recent commits)
- What appears to be the next step

---
description: Investigate a problem thoroughly and ship a well-tested fix
argument-hint: "<issue-number | URL | description>"
allowed-tools: Read, Write, Edit, MultiEdit, Bash(*), Grep, Glob, Agent, WebSearch, WebFetch
---

# Fix: Thorough Investigation → Tested PR

Take any input, understand it deeply, verify the fix is safe, then ship.
This is not a quick fix. Take your time. Get it right.

## Phase 1: Understand the Input

Detect what `$ARGUMENTS` is and fetch context:

- **GitHub issue number** (e.g. `322`):
  `gh issue view $ARGUMENTS --json title,body,labels,assignees,url`

- **GitHub issue URL** (contains `/issues/`):
  Extract the number, then `gh issue view`.

- **GitHub PR URL** (contains `/pull/`):
  Read as a problem description, not code to merge.
  `gh pr view <number> --json title,body,files,url`

- **GitHub discussion URL** (contains `/discussions/`):
  `gh api repos/{owner}/{repo}/discussions/<number>`

- **CodeQL / Dependabot / security alert URL**:
  `gh api repos/{owner}/{repo}/code-scanning/alerts/<number>` or
  `gh api repos/{owner}/{repo}/dependabot/alerts/<number>`

- **Plain text description**: use as-is.

## Phase 2: Deep Investigation (Plan Mode)

Enter plan mode. Do not write code yet.

### 2a. Research the problem

- Read every source file involved in the problem. Trace the full code
  path from entry point to the affected behavior.
- Read existing tests covering the area. Understand what is already
  tested and what is not.
- If the problem involves external APIs, upstream behavior, or library
  quirks: use web search, firecrawl, context7, or fetch to find
  documentation, changelogs, known issues, or prior discussions.
- If the problem involves an upstream *arr API: check the vendored
  OpenAPI specs in `docs/api/` first.

### 2b. Triage: is this ours to fix?

Not every issue needs a code change. After investigating, determine
which category this falls into:

**A. Bug in Houndarr** — proceed to 2c.

**B. Not a Houndarr bug** (upstream *arr issue, user misconfiguration,
environment problem, Docker/OS issue):
- Reply explaining what you found, with evidence (code traces,
  payload verification, upstream source references).
- Include actionable steps the reporter can take (curl test, version
  upgrade, upstream issue link, config change).
- Add the `waiting-for-reporter` label.

**C. Out of scope** (feature request for download clients, indexer
management, multi-user, media file manipulation, or anything outside
Houndarr's single-purpose scope):
- Reply explaining why it is out of scope, referencing the scope guard.
- Close the issue.

**D. Insufficient information** (cannot reproduce, missing logs,
unclear steps):
- Reply asking for the specific information needed.
- Add the `waiting-for-reporter` label.

**E. Duplicate** (same root cause as an existing open issue):
- Reply linking the original issue.
- Close as duplicate.

If the triage result is B, C, D, or E: execute the actions above
immediately. Do not create a branch or write code. Stop after
completing the triage action.

If the triage result is A: continue.

### 2c. Assess scope

- What is the minimal change needed?
- What files will be touched?
- Are there related areas that could be affected?

### 2e. Identify edge cases and breaking changes

Think through every scenario where the fix could go wrong:

- What happens with empty input, null values, missing data?
- What happens with each instance type (Radarr, Sonarr, Lidarr,
  Readarr, Whisparr)?
- Does this change any public behavior (API responses, UI, config
  defaults, database schema)?
- Does this break existing tests? If so, is the breakage correct
  (tests were testing wrong behavior) or a regression?
- Could this change affect the search loop timing, cooldown logic,
  or queue backpressure?
- If touching auth, crypto, or SSRF validation: stop and flag for
  extra review before proceeding.

### 2f. Present the plan

Summarize:
- What the problem is
- What the root cause is (or your best hypothesis)
- What the fix will change
- Which files will be modified
- Which edge cases you considered and how they are handled
- Any breaking changes and how they are mitigated

Wait for confirmation before proceeding. Do not implement until the
plan is approved.

## Phase 3: Tracking Issue

If the input was NOT a GitHub issue (discussion, PR, alert, or plain
text), ask whether to:

- Create a tracking issue, or
- Proceed without one

If creating an issue:
```
gh issue create --title "type: description" \
  --body "..." --label "type: bug" --label "priority: medium"
```

## Phase 4: Implement

Exit plan mode. Create a feature branch:

```
git fetch origin
git checkout -b type/short-slug origin/main
```

Implement the fix. Follow AGENTS.md conventions. Make the minimum
change needed to solve the problem correctly.

## Phase 5: Test Thoroughly

Write tests that cover:

- The happy path (the fix works)
- The regression case (the original bug does not recur)
- Every edge case identified in Phase 2c
- Boundary conditions

Run all five quality gates:

```
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m bandit -r src/ -c pyproject.toml
.venv/bin/pytest
```

If any gate fails, fix the issue. Do not proceed with failures.

## Phase 6: Final Review

Before committing, review your own changes:

- Re-read every modified file. Does the change do only what it should?
- Are there any unintended side effects?
- Did you introduce any new noqa/type:ignore suppressions? If so, are
  they justified?
- Does the change match surrounding code patterns?

## Phase 7: Ship

Commit with Conventional Commits format:

```
git add <specific files>
git commit -m "type(scope): description

Closes #N"
```

Push and create the PR:

```
git push -u origin HEAD
gh pr create --title "type(scope): description" --body "<filled-in template>"
```

Use the PR template at `.github/pull_request_template.md` as the
structure for the body. Fill in each section, replace HTML comments
with actual content, link the issue with `Closes #N`, check the
correct Type of Change box, and check only the Checklist items that
actually apply.

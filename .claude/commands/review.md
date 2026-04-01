---
description: Run all five quality gates and report results
allowed-tools: Bash(.venv/bin/python *), Bash(.venv/bin/pytest *)
---

# Full Quality Gate Review

Run every quality check in sequence. Report results clearly.
Stop and fix if any check fails.

## 1. Lint

```
.venv/bin/python -m ruff check src/ tests/
```

Report: pass or fail with error count.

## 2. Format Check

```
.venv/bin/python -m ruff format --check src/ tests/
```

If files are unformatted, run `.venv/bin/python -m ruff format src/ tests/`
to fix, then re-check.

## 3. Type Check

```
.venv/bin/python -m mypy src/
```

Report: pass or fail with error count and locations.

## 4. SAST

```
.venv/bin/python -m bandit -r src/ -c pyproject.toml
```

Report: pass or fail with any findings.

## 5. Tests

```
.venv/bin/pytest
```

Report: total tests, passed, failed, skipped.

## Summary

Present a single table:

| Check | Result | Details |
|-------|--------|---------|
| Lint (ruff) | ... | ... |
| Format (ruff) | ... | ... |
| Type check (mypy) | ... | ... |
| SAST (bandit) | ... | ... |
| Tests (pytest) | ... | ... |

If all pass, say "All quality gates pass." and nothing else.
If any fail, list the specific failures and suggest fixes.

Do NOT recite the exact test counts in a "reporting" style.
Just say whether things pass or what broke.

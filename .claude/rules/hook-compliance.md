---
description: How to work with settings.json hooks without bypassing them
---

# Hook Compliance

The project's `.claude/settings.json` defines 7 hooks that enforce
formatting, type checking, branch protection, and code quality. These
hooks only fire on the tools they match. Using the wrong tool for a
task can silently bypass safety checks.

## Bash file writes bypass Write/Edit/MultiEdit hooks

Hooks 1, 2, and 5 match `Write|Edit|MultiEdit` only. They do NOT fire
when files are created or modified via Bash (`cat >`, `echo >`, `tee`,
`sed -i`, `printf >`, heredocs, or any other shell redirection).

Hooks defeated by Bash file writes:

- Hook 1 (PostToolUse): auto-formats `.py` files with `ruff format`
  and `ruff check --fix`. Skipping this means the file stays
  unformatted until the next explicit format run.
- Hook 2 (PostToolUse): runs `mypy src/` when `src/**/*.py` files
  change. Skipping this means type errors go unreported until the next
  explicit mypy run.
- Hook 5 (PreToolUse): blocks editing repo files on the `main` branch
  (only AGENTS.md and CLAUDE.md are allowed). Skipping this defeats
  branch protection entirely.

Hook 6 (format check before `git commit`) fires on `Bash` matching
`^git commit`, so it still catches unformatted files at commit time
regardless of how they were written. This is the backstop, not the
primary defense.

## When to use Write/Edit/MultiEdit vs Bash

Use Write, Edit, or MultiEdit for all file creation and modification.
These are the correct tools and they trigger all hooks.

Use Bash for running commands, not writing files. Acceptable Bash uses:
shell commands, git operations, test runners, linters, build tools.

Do not use Bash to write files as a workaround when a hook blocks an
action. If hook 5 blocks an edit on main, create a feature branch
first. If hook 6 blocks a commit, run `ruff format` first. The hooks
exist to catch mistakes; working around them reintroduces the mistakes.

## What to do when a hook blocks an action

- Hook 5 blocks a file edit on main: create a feature branch
  (`git checkout -b type/slug`) and make the edit there.
- Hook 6 blocks a commit: run `.venv/bin/python -m ruff format src/ tests/`
  to fix formatting, then retry the commit.
- Hook 3 blocks `rm -rf`: use a more specific path instead of a
  dangerous wildcard target.
- Hook 4 blocks a git push: you are pushing to main or force-pushing.
  Push to a feature branch and open a PR instead.

In every case: fix the underlying issue, do not bypass the hook.

## Other edge cases

- Hook 7 (Stop): warns on TODO/FIXME/placeholder/stub in responses.
  This is a case-insensitive grep on the full response text, including
  code blocks and file paths. A path like `src/todo_handler.py` or a
  quoted `# TODO` from existing code will trigger the warning. This is
  a low-severity false positive; acknowledge the warning and continue
  if the marker is in quoted or referenced text, not in new code you
  are leaving unfinished.
- Hook 1 (auto-format): only runs on `.py` files. Non-Python files
  (`.md`, `.yml`, `.json`, `.html`) are silently skipped.
- Hook 2 (mypy): only runs when `src/**/*.py` files are changed. Test
  file edits do not trigger mypy. This is intentional (tests are exempt
  from ANN rules).
- Hook 6 (format check): if `.venv` does not exist, the hook blocks the
  commit. This is correct; set up the project before committing.

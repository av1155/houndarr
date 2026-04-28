---
description: Bump version and prepare a release PR
argument-hint: "<patch|minor|X.Y.Z>"
allowed-tools: Read, Write, Edit, Bash(git *), Bash(gh *), Bash(date *), Bash(.venv/bin/python *), Bash(.venv/bin/pytest *)
---

# Bump Version and Release

Prepare a release PR following the Houndarr versioning workflow.

Houndarr follows Keep a Changelog 1.1.0: every shipped PR adds a
bullet under `## [Unreleased]` as part of its own commit, so by the
time `/bump` runs, the upcoming release's bullets are already in the
file.  This command's job is to **promote** the Unreleased block to a
versioned block, not to draft one from PR titles.

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

## 3. Verify the Unreleased Block

### 3a. Refuse to bump an empty Unreleased

```
awk '/^## \[Unreleased\]$/{found=1; next} found && /^## \[/{exit} found{print}' CHANGELOG.md
```

If the extracted block contains nothing more than the trailing `---`
separator, stop with `"## [Unreleased] is empty. Nothing to release."`
and do not touch VERSION or CHANGELOG.

### 3b. Verify every bullet's claim before promoting

**This step is mandatory. Skipping it is how inaccurate release
notes ship.** The v1.9.0 release went out with two factually wrong
bullets (#409 said "the CHANGELOG block for the current version"
when the code returns every block since `last_seen`; #395 said
"spreads picks across the whole backlog each cycle" when the code
picks one random page per cycle) because the draft was built from
PR titles and memory. Fixing it required a CHANGELOG-correction PR,
a GitHub Release delete, a tag re-cut, and a Docker image rebuild.

For every PR referenced in the Unreleased block:

```bash
gh pr view N --repo <owner>/<repo> --json title,body
gh pr diff N --repo <owner>/<repo> | head -400
```

For any bullet that claims behavior (a default, a UI element, an
error string, an API response shape), also read the relevant source
file and pin the claim to a specific `file:line`.

**Adopt the PR author's vocabulary for nuance.** If the PR body
says "new default for fresh installs; existing instances keep their
prior behaviour," the bullet says "new default for newly added
instances," not "new default." The distinction is load-bearing for
upgraders.

**Rule of thumb:** every bullet must be defensible with a concrete
reference: a PR-body sentence, a diff fragment, or a source
`file:line`. If you cannot cite one, rewrite the bullet or drop it.

If the Unreleased block has any bullets that no longer reflect the
current code (e.g. a feature was reverted between merge and bump),
edit them in place before continuing.

### 3c. Final filter and language pass

The /ship workflow should already filter out non-user-facing changes
and apply the project style guide when adding to Unreleased.  Double-
check here, against the **Changelog style guide** in `AGENTS.md`
(`## Versioning, Changelog & Releases` → `### Changelog style guide`).

**Keep only user-facing entries:**

- Features (Added)
- Bug fixes (Fixed)
- Behavioural / UI / config changes (Changed)
- Removed functionality (Removed)
- Deprecations (Deprecated)
- Security fixes (Security)

**Drop:**

- CI changes, pure refactors, test-only changes, docs-only changes
- Chore / infrastructure work that does not affect the operator
- Dependency bumps with no security or behaviour impact

If after filtering the Unreleased block has no user-facing entries,
report `"No user-facing changes in Unreleased. Nothing to release."`
and stop.

**Language pass:**

Walk every remaining bullet against the AGENTS.md style guide.
Flag and rewrite any bullet that:

- References an internal Python class, private helper, or `src/...`
  file path (rewrite to describe the user-visible behaviour).
- Exceeds 250 characters or stretches across more than one sentence
  without a migration / upgrade reason (split it).
- Uses banned phrasings: "various", "improved", "enhanced", "better",
  marketing adverbs ("seamlessly", "robustly", "significantly"),
  empty verbs ("leverages", "utilizes"), bold lead-ins, marketing
  trail clauses, em dashes.
- Reads as past-tense narration ("We added...") instead of noun-led
  present-tense state ("Logs page distinguishes...").

**Format reminder:**

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added

- One sentence per bullet. (#N)

### Changed

- One sentence per bullet. (#N)

### Fixed

- One sentence. User-facing impact first. Issue/PR ref at end (#N).

### Removed

- One sentence per bullet. (#N)

---
```

**Rules:**

- Every bullet must be justified by a PR-body sentence, a diff
  fragment, or a source `file:line`. Do not draft from PR titles,
  commit messages, or memory alone. See §3b.
- Allowed `###` headers: `Added`, `Changed`, `Deprecated`, `Removed`,
  `Fixed`, `Security` only. Omit any section that has no entries.
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

## 4. Present the Promotion Plan for Review

Show me a unified diff of the planned CHANGELOG.md change: the renamed
heading (`## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`), any bullet
edits made in §3b, and the fresh empty `## [Unreleased]` block that
will sit above it.  Wait for my approval or edits.  Do not proceed
until I confirm.

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

## 7. Promote Unreleased in CHANGELOG.md

Two edits, in this order:

1. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (today's date,
   ISO 8601).
2. Insert a fresh, empty Unreleased block at the top, directly after
   the file header:

   ```markdown
   ## [Unreleased]

   ---

   ## [X.Y.Z] - YYYY-MM-DD
   ```

The fresh block intentionally has only the heading + `---` separator;
the version-check workflow is fine with an empty body so long as the
trailing `---` is present.

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

Version bump to X.Y.Z. Only VERSION and CHANGELOG.md are modified;
the changelog promotes the existing `## [Unreleased]` block to a
versioned heading and reseeds an empty Unreleased above it.

## Release notes (from CHANGELOG)

<paste the CHANGELOG entries here>

## Checklist

- [x] Linked issue has `type:*` and `priority:*` labels
- [x] Only VERSION and CHANGELOG.md changed
- [x] CHANGELOG entry follows existing format
- [x] Fresh `## [Unreleased]` block reseeded above the new version
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

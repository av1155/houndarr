---
name: houndarr-changelog
description: Houndarr's CHANGELOG.md style guide and entry rules. Loads when reading or editing CHANGELOG.md or VERSION. Covers the noun-led present-tense voice, the 80-160 character length target, vocabulary the operator can act on, banned phrasings, what does not belong in a changelog, separator rules, and the bullet-justification protocol that prevents drafting from PR titles or memory.
paths:
  - "CHANGELOG.md"
  - "VERSION"
---

# Houndarr changelog conventions

The audience is self-hosters and homelab operators running Houndarr in
Docker or Kubernetes alongside the *arr stack. They read config files,
env vars, log lines, and SQLite schemas; they do not read the Python
source. Tune every bullet for that reader.

## Voice (noun-led, present tense)

The category heading carries the verb (`### Added`, `### Fixed`,
`### Changed`). Bullets describe the post-change state from the
reader's vantage point, not the maintainer's action:

- Good: `Logs page distinguishes a fresh install (No log entries yet)
  from a filter that matches nothing (No entries match those filters.). (#566)`
- Avoid: `We added a fresh-install vs filter-empty distinction to the
  logs page.` (narrator voice)
- Avoid: `Distinguish fresh-install from filter-empty on the logs
  page.` (imperative; reserved for SDK changelogs)

This matches the convention used by Authelia, AdGuard Home, Plausible,
Caddy, and other self-hosted tools targeting the same audience.

## Length

- Target: 80 to 160 characters per bullet.
- Hard ceiling: 250 characters. A bullet longer than that must split
  into two unrelated bullets, or the second clause moves to the PR body.
- One sentence per bullet. A second sentence is permitted only when
  a migration or upgrade-affecting consequence must ride with the
  change (rare).

## Vocabulary the operator can act on, only

- **Use**: env var names (`HOUNDARR_COOKIE_SAMESITE`), config keys, schema
  version numbers (`Schema v16`), database column names that survive
  in the SQLite file (`monitored_total`, `whisparr_episode`), HTTP
  routes (`/api/status`), log strings the operator can grep
  (`hourly limit reached (N/hr)`), CVE IDs, dependency versions when
  a security or behaviour change ties to the bump.
- **Avoid**: internal Python class names (`InstanceValidationError`,
  `AuthMiddleware._dispatch_proxy`), private helpers
  (`_redirect_guard`, `_run_search_pass`), file paths under `src/`,
  module attribute names that have no user surface. Describe the
  user-visible behaviour instead.
- **Borderline**: public library types that surface in tracebacks
  (`httpx.TransportError`). Allowed when the user actually sees the
  type name in their logs, otherwise paraphrase to "transport-level
  error".

## Banned phrasings (drift signals; rewrite or drop)

- Vague: "Various bug fixes", "Minor improvements", "Misc updates",
  "Bug fixes and stability improvements".
- Marketing: "We are thrilled to...", "delightful new experience",
  "groundbreaking", "exciting".
- Magic adverbs without measurement: "seamlessly", "robustly",
  "significantly", "dramatically". Either quantify ("reduces idle
  CPU by 60%") or omit.
- Empty verbs: "leverages", "utilizes", "harnesses", "facilitates".
  Pick the concrete verb.
- Vague comparatives: "Improved error handling", "Enhanced UX",
  "Better performance". Name the change: "Connection errors now
  log at WARNING with the instance name."
- Bold lead-ins: `**Performance:** faster X`. Plain bullet.
- Marketing trail clauses: "for a smoother experience". Stop at the
  technical fact.
- Past-tense narration: "We added...", "We fixed...". Drop the
  pronoun.
- Em dashes anywhere (project-wide rule; use a colon, semicolon,
  comma, period, or parentheses).

## What does NOT belong in the changelog

- Pure refactors with no user-visible behaviour change (Common
  Changelog explicitly excludes these; they live in PR bodies).
- Test-only changes.
- Docs-only changes (the docs site has its own deploy log).
- CI / workflow changes that do not affect deployers.
- Dependency bumps with no security or behaviour impact.

## Schema version bumps

When a release ships a SQLite schema migration, name the schema
number, what the migration touches, and any rollback constraint. An
AdGuard-Home-style "to roll back, downgrade to <previous tag>" line
helps operators who restore from a backup.

## Examples (verbatim from the repo, judged)

- Exemplary: ``Helm chart `appVersion` is now prefixed with `v` so it
  matches the published Docker image tags. (#364)`` (102 chars; named
  user-visible attribute; one causal clause).
- Exemplary: ``Hourly rate-limit skip rows now read `hourly limit
  reached (N/hr)` across missing, cutoff, and upgrade passes. (#491)``
  (names the exact log string the operator greps for).
- Over-technical (rewrite before merging): ``Curated
  `InstanceValidationError.public_message` text replaces the raw
  exception in instance validation banners`` should read ``Instance
  validation banner shows a curated message instead of the raw Python
  exception``.
- Over-technical (rewrite): ``Random search order now uses a
  stratified-shuffle page deck plus partial-page sentinel padding``
  should read ``Random search order spreads dispatch probability
  uniformly across the backlog so no page is over- or under-selected``.

## CHANGELOG entry rules

CHANGELOG.md always carries a `## [Unreleased]` section above every
versioned block:

```markdown
## [Unreleased]

### Added

- One sentence per bullet. (#N)

### Fixed

- One sentence. User-facing impact first. Issue/PR ref at end (#N).

---

## [X.Y.Z] - YYYY-MM-DD

### Added

- One sentence per bullet. (#N)

### Changed

- One sentence per bullet. (#N)

### Fixed

- One sentence per bullet. (#N)

### Removed

- One sentence per bullet. (#N)

---
```

**Allowed `###` headers (Keep a Changelog 1.1.0):** `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed`, `Security`. Level-4 `####` subheadings
may group items within `###` sections for major releases. Omit any
section that has no entries.

## Bullet rules

- Add the bullet to `## [Unreleased]` as part of the same PR that
  ships the change. /bump promotes the accumulated Unreleased block
  to a versioned heading at release time.
- **Every bullet must be justified by a PR-body sentence, a diff fragment,
  or a source `file:line`.** Do not draft from PR titles, commit messages,
  or memory alone. The verification protocol lives in
  `.claude/commands/bump.md` §3b; skipping it is what shipped the
  inaccurate v1.9.0 bullets that had to be corrected in #420.
- Adopt the PR author's vocabulary for nuance. If the PR body says
  "new default for fresh installs; existing instances keep their prior
  behaviour," the bullet says "new default for newly added instances,"
  not "new default."
- One sentence per bullet; no multi-line prose.
- Lead with user-facing impact, not implementation details.
- End with `(#N)` issue/PR reference.
- Use backticks for identifiers, file names, env vars, UI elements.
- Use markdown `[text](url)` syntax for links; bare URLs do not auto-link
  in the in-app `What's New` modal (GitHub's CHANGELOG view autolinks both,
  but the modal's `_render_changelog_bullet` filter only accepts the
  `[text](url)` form).
- Be specific: `Connection errors now log at WARNING with instance name`
  not `Improved error handling`.

## Separators

Both `## [Unreleased]` and every versioned block end with a `---` line
(blank line before and after). The fresh Unreleased block reseeded by
/bump carries only the heading and the trailing `---`.

## Non-user-facing PRs

CI-only, refactor-only, test-only, docs-only, and chore/infrastructure
changes do not get a Changelog bullet. The /ship workflow filters these
out automatically.

## CI-enforced validation

1. **PR-time** (`version-check.yml`): Runs on PRs touching `VERSION` or
   `CHANGELOG.md`. Validates VERSION format, requires `## [Unreleased]`
   as the topmost `## [...]` block, validates the Unreleased block's
   `###` headers + trailing `---` separator, and validates the
   `## [VERSION] - YYYY-MM-DD` block matches VERSION with valid `###`
   headers + trailing `---`.
2. **Tag-time** (`release.yml`): Validates VERSION == tag, extracts the
   `## [X.Y.Z]` block via `awk`, creates GitHub Release using
   `--notes-file` (avoids backtick shell substitution).

The in-app `What's New` modal parser (`src/houndarr/services/changelog.py`)
silently skips `## [Unreleased]` because the heading lacks the `X.Y.Z`
plus ISO-date suffix that `_VERSION_HEADING` requires; users only see
versioned blocks until /bump promotes Unreleased.

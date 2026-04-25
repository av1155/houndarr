# Code Commenting Standard — Houndarr Stack

## Universal Principles (apply to every language below)

**The core rule: comments explain _why_, code explains _what_.** If a comment
just restates the code, delete it and rename the variable/function instead.

Write comments for:

- **Intent / rationale** — why this approach, not the obvious alternative
- **Non-obvious constraints** — rate limits, race conditions, API quirks
- **Warnings** — "do not call without holding the lock", "mutates input"
- **Links** — issue numbers, RFC/spec refs, upstream bug URLs
- **TODO/FIXME/HACK/XXX** — tagged with author + issue, e.g.
  `# TODO(av1155, #123): switch to async pool once Sonarr v5 ships`

Avoid:

- Restating the code (`i += 1  # increment i`)
- Commented-out code (use Git; delete it)
- Changelog-style comments (`# 2026-01-15 av1155: fixed bug`) — that's Git's job
- Decorative banners (`# ========== SECTION ==========`) — use headings/modules
- Stale comments — if you change code, update or remove the comment
- Comments apologizing for the code ("this is hacky but...") — fix it or file a ticket
- Obvious type/name duplication (`user_id: int  # the user ID`)

---

## Python (FastAPI, backend, scheduler)

**Style:** PEP 8 + PEP 257 + Google-style docstrings (parsed by Sphinx's
Napoleon; industry default for FastAPI projects in 2026).

**Comments (`#`):**

- Full sentences, capitalized, ending with a period
- Block comments indented to the code they describe
- Inline comments separated by **two** spaces: `x = 2  # index offset`
- Use `# type: ignore[code]` with the specific error code, never bare
- Use `# noqa: RULE` with the Ruff rule ID, never bare

**Docstrings (`"""..."""`):**

- Required on every public module, class, function, and FastAPI route
- Summary line in **imperative mood** ("Return the queue depth", not "Returns...")
- Blank line, then details
- Google style sections: `Args:`, `Returns:`, `Raises:`, `Yields:`, `Example:`
- Don't repeat type hints in the docstring — hints are the source of truth;
  the docstring explains meaning and edge cases
- FastAPI routes: the docstring becomes the OpenAPI description — keep the
  first line short and user-facing

```python
async def search_missing(
    instance_id: int,
    batch_size: int = 5,
) -> SearchResult:
    """Trigger a polite batch search for missing items on an *arr instance.

    Picks items at random (default) or chronologically based on the
    instance's configured search order, respecting the per-item cooldown
    and hourly API cap.

    Args:
        instance_id: Primary key of the instance in the local DB.
        batch_size: Items to search this cycle. Kept small on purpose to
            avoid indexer bans — see docs/rate-limiting.md.

    Returns:
        SearchResult with items queued and items skipped by cooldown.

    Raises:
        InstanceNotFoundError: No instance with this ID.
        RateLimitExceeded: Hourly API cap hit; caller should back off.
    """
```

---

## HTML / Jinja Templates

- Use `<!-- -->` for structural markers only (`<!-- main nav -->`,
  `<!-- /main nav -->`) — helps when scanning deeply nested markup
- Use Jinja's `{# ... #}` for developer notes — **never** use `<!-- -->` for
  anything secret or internal; HTML comments ship to the browser
- Don't comment every `<div>`; semantic tags (`<nav>`, `<article>`, `<aside>`)
  are self-documenting
- Mark template blocks: `{% block content %}{% endblock content %}` (name the
  `endblock` — acts as a comment for long templates)

---

## HTMX

HTMX attributes are declarative and largely self-documenting. Only comment when:

- The swap target is non-obvious (`hx-target="closest tr"` — explain why)
- There's an out-of-band swap chain
- A trigger uses a custom event dispatched from server-sent HTML

```html
<!-- Polls every 10s only while dashboard tab is visible;
     server returns 286 to stop polling once run completes. -->
<div
    hx-get="/api/runs/status"
    hx-trigger="every 10s [document.visibilityState==='visible']"
    hx-swap="outerHTML"
></div>
```

---

## Tailwind CSS (v4)

- **Don't** comment individual utility classes — the class name _is_ the doc
- **Do** comment _why_ a non-obvious utility combo exists, above the element
- Long class strings (>10–12 utilities): extract to a component or a
  `@utility`/`@apply` rule in your CSS layer rather than commenting
- In `@layer` blocks and `@theme` definitions, comment custom tokens with
  their design intent, not their value

```html
<!-- Sticky header offset matches the 56px app bar; must update together. -->
<div class="sticky top-14 z-30 bg-slate-900/80 backdrop-blur"></div>
```

---

## Dockerfile

- `#` comments; one blank line between logical stages
- Comment **rationale** for each `RUN` that pins versions, cleans caches, or
  uses a workaround — future maintainers need to know if it's safe to touch
- Don't comment what `COPY`, `WORKDIR`, `EXPOSE` obviously do
- Add `# syntax=docker/dockerfile:1` at the very top (not a comment per se —
  it's a parser directive, but lives in comment syntax)

```dockerfile
# Multi-stage: builder compiles wheels so the final image stays slim.
FROM python:3.13-slim AS builder
...

# Drop to non-root after PUID/PGID remap via entrypoint.
USER houndarr
```

---

## docker-compose.yaml / YAML configs

- `#` comments; YAML has no multi-line comment syntax — prefix each line
- Comment **non-obvious** keys only: why a port/volume/env is required, what
  breaks if it's changed, links to docs
- Don't comment `image:`, `restart:`, `ports:` — they're self-evident
- Group related settings and add a one-line section header above them
- Keep example/reference values in comments, not code:
  `TZ: America/New_York  # IANA tz; see https://...`

```yaml
services:
    houndarr:
        image: ghcr.io/av1155/houndarr:latest
        # Required: persistent store for encrypted API keys + SQLite DB.
        # Must survive container recreation or users lose their master key.
        volumes:
            - ./data:/data
```

---

## SQL / Migrations

- Every migration file starts with a header comment: purpose, ticket, author
- Inline `--` comments for non-obvious indexes, cascade choices, or schema
  decisions (why a column is nullable, why a unique constraint exists)
- Never comment out DDL "just in case" — use a new migration to revert

---

## Shell / bash scripts

- `#!/usr/bin/env bash` then `set -euo pipefail` then a **header comment**
  describing purpose, required env vars, and exit codes
- Comment any `|| true`, `2>/dev/null`, trap, or subshell — these are where
  bugs hide

---

## Markdown (docs, READMEs)

- Use `<!-- -->` sparingly — for TOC markers, editor hints, or
  `<!-- markdownlint-disable rule -->` pragmas
- Don't hide prose in HTML comments; if it's worth writing, put it on the page

---

## Quick smell test before committing a comment

1. Does the code already say this? → delete
2. Could a better name eliminate this? → rename
3. Will this still be true in 6 months? → if unsure, link an issue instead
4. Would a new contributor understand _why_ from this comment? → good, keep it

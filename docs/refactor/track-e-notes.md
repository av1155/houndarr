# Track E implementation notes

Context captured during Track E's Jinja macro extraction that does
not belong in a source docstring or commit body but is load-bearing
for future work in this area.

## Dashboard `dash-pill` is a separate visual system

The dashboard's per-instance Active / Error / Disabled pill
(`dash-pill`, `dash-pill--active`, `dash-pill--error`,
`dash-pill--disabled`, `dash-pill__dot`) is deliberately not
represented in `src/houndarr/templates/_macros/badges.html`, and no
batch in Track E migrates it.

Why it stays inline:

1. The pill is rendered by JavaScript, not Jinja. Each card's markup
   is built in `static/js/dashboard.js::renderStatusPill`, which
   receives an instance snapshot from `GET /api/status` and appends
   a string to the DOM. A Jinja macro cannot reach that render
   path, and rewriting the dashboard to server-render its cards
   would be a scope change well beyond "extract shared markup".
2. The CSS is a bespoke BEM system. `.dash-pill--error` has a hover
   state, an SVG icon rule, and a pointer cursor; `.dash-pill--disabled`
   uses the `dash-pill__dot` child. The rules live in a `<style>`
   block inside `partials/pages/dashboard_content.html` (and a
   matching mirror in `static/css/app.css`). They do not compose
   with the Tailwind utility classes the `_macros/badges.html`
   `status_pill` macro emits.
3. The semantic scope differs. The server-rendered `status_pill`
   (used by the settings instance table) signals "is the instance
   enabled for searching right now?" The dashboard `dash-pill`
   additionally renders an error count and a link into the logs.
   Sharing a macro would force one of the two surfaces to pick up
   markup it does not use.

If a future change needs to unify the two, the right seam is to
server-render dashboard cards and retire the JavaScript renderer.
Until then, the dashboard system is its own thing, and Track E is
complete without it.

## Macros landed in Track E.1..E.4

`_macros/badges.html` hosts:

- `instance_type_badge(type_value)`: Sonarr/Radarr/Lidarr/Readarr/
  Whisparr v2/Whisparr v3 type chip. Consumed by
  `partials/instance_row.html`.
- `log_action_badge(action)`: searched/skipped/error badge with an
  `info` fallback. Consumed by `partials/log_rows.html`.
- `log_kind_badge(kind)`: missing/cutoff/upgrade badge with a dash
  fallback. Consumed by `partials/log_rows.html`.
- `log_trigger_badge(trigger)`: scheduled/run_now/system badge with
  a dash fallback. Consumed by `partials/log_rows.html`.
- `log_cycle_outcome_badge(progress)`: progress/no_progress badge
  with an `unknown` fallback. Consumed by `partials/log_rows.html`.
- `status_pill(state)`: active/error/disabled pill with
  `station-pulse-dot`. Consumed by `partials/instance_row.html` in
  a later batch (E.16 introduces `_macros/instances.html` which
  wraps `instance_row` and picks up `status_pill`).

Byte-equal render pinning lives at
`tests/test_templates/test_macros_badges.py`. Every branch of every
macro, including unmapped strings and `None`, has an explicit
assertion.

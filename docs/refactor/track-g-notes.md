# Track G implementation notes

Context captured during Track G's Tailwind `@layer components` and
`@utility` work that does not belong in a source docstring or commit
body but is load-bearing for future work in this area.

## Why `.action-badge` was skipped

An early version of the Track G plan reserved space for an
`.action-badge` component class to deduplicate the log-row badge
bundle (`badge badge-soft badge-<state>`) that `log_action_badge`,
`log_kind_badge`, `log_trigger_badge`, and `log_cycle_outcome_badge`
emit from `_macros/badges.html`. The class never landed because
daisyUI's own badge primitives already cover this shape.

The unlayered daisyUI overrides in `src/houndarr/static/css/input.css`
already retune `.badge`, `.badge-soft.badge-success`,
`.badge-soft.badge-warning`, `.badge-soft.badge-error`,
`.badge-soft.badge-info`, `.badge-soft.badge-primary`, and
`.badge-soft.badge-neutral` for Houndarr's dark surface (chip
radius, monospace font, tuned state-bg and state-border tokens).
Every log-row badge macro composes two of those classes, e.g.
`badge badge-soft badge-success`, and picks up the Station visual
language for free.

Introducing a `.action-badge` component on top of that would either:

1. Duplicate the daisyUI primitives it already rides on (e.g.
   `@apply badge badge-soft badge-success` wrapped in a new class),
   which is pure indirection. The call site still has to pick a
   state, and the shared bundle is exactly one token (`badge-soft`)
   long.
2. Replace the daisyUI classes entirely, which would force every
   future daisyUI upgrade to re-verify that Houndarr's custom token
   pipeline matches what the plugin produces. The unlayered-override
   approach means daisyUI stays the source of truth for badge
   semantics and Houndarr only tunes the visual tokens.

Either path adds surface without removing duplication. The one-
token `badge-soft` prefix across the four macros is small enough
that a component class does not pay back.

The `.field-label` and `.status-pill` components promoted in G.2
and G.3 do pay back because the bundles are 7 and 5 utilities long
respectively and have no daisyUI analogue to ride on. Badges are
genuinely different: daisyUI owns the primitive, Houndarr tunes the
tokens, and the macro stays terse.

## Macros touched in Track G

G.2 updates `form_field` and `select_field` in
`_macros/forms.html`: the default `label_class` argument switches
from the inline `block text-xs font-medium text-slate-400 mb-1.5
uppercase tracking-wide` bundle to `field-label`. Callers that pass
an explicit `label_class=` override are unaffected (e.g. the admin
confirm dialog's `block text-xs font-medium text-slate-400 mb-1.5`
label without uppercase/tracking-wide stays opt-in).

G.3 updates `status_pill` in `_macros/badges.html`: the three arms
(active, error, disabled) now emit `status-pill status-pill--<state>`
instead of the long inline flex bundle. The inner dot keeps its
Tailwind utilities inline because dot colour tracks the variant and
the `station-pulse-dot` animation toggle is per-variant.

Byte-equal render pinning for both macros lives at
`tests/test_templates/test_macros_forms.py` and
`tests/test_templates/test_macros_badges.py`. Every assertion there
reflects the post-G.2 / post-G.3 output.

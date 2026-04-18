---
sidebar_position: 3
title: Search Order
description: Why Houndarr defaults to Random search order, how Chronological rotation works, and when to pick each.
---

# Search Order

Controls how items are picked from the wanted list each cycle. The
setting applies to all three passes (missing, cutoff, and upgrade)
for an instance. Two options: Random (default for fresh installs)
and Chronological.

## Random (default)

Each cycle, Houndarr probes the wanted-list total, picks a random
page within that range, fetches it, and shuffles the items before
evaluation. Over many cycles the search distribution spreads evenly
across the catalogue instead of moving one shelf at a time.

Recommended for most users, especially anyone whose library has
long alphabetical runs of similarly-dated items: a full series
binge-added the same day, or a back catalogue imported in one go.
Without shuffling, those items appear as a clustered wall in the
logs.

For the upgrade pass, Random replaces the id-sort-plus-offset
rotation with a plain shuffle of the upgrade pool. The persisted
offset is still maintained so switching back to Chronological
resumes from a sane position.

## Chronological

Walks the wanted list oldest-first, paged against the persisted
offset (`missing_page_offset`, `cutoff_page_offset`). Deterministic
and easy to reason about: you can predict which items come up next.
The trade-off is that same-day releases cluster visibly because the
*arr API falls back to title order within equal dates.

Pick Chronological when you want predictable coverage for
debugging, or when you rotate through a relatively small wanted
list where Chronological order already gives reasonable spread.

## What Random does not change

- Cooldowns, hourly caps, post-release grace, queue backpressure,
  and the allowed time window all apply identically in both modes.
- Shuffling happens after the page is fetched from your *arr
  instance, so no extra indexer pressure is introduced.
- Random mode adds one lightweight probe call per pass (a
  `pageSize=1` request to read `totalRecords`). Whisparr v3 reuses
  its cached movie list for this count at no extra cost.

## Why page-offset rotation exists (Chronological only)

Before page-offset rotation, the missing and cutoff passes always
started from wanted-list page 1. When the first few pages were
entirely on cooldown, items further down the list never got
evaluated.

Concretely: a 500-item missing list with a 5-page scan cap and
page-size-40 would stall on the same alphabetical first 200 items
cycle after cycle while later items languished. Users reported this
as "Houndarr keeps searching the same 80 movies and ignores the
rest" (discussion #292).

Page-offset rotation fixes this. Each pass remembers which API page
it stopped at and starts from there on the next cycle. When the
offset runs past the end of available data, it wraps back to page
1 within the same cycle so nothing is permanently skipped. The
upgrade pass already had this treatment; the fix extended it to
missing and cutoff passes.

Offsets reset to page 1 when you save instance settings. Toggle
upgrade search off to reset the upgrade offset to zero.

## Fair backlog scanning

Houndarr does not stop at the first wanted page. During each cycle,
the engine scans deeper pages when top candidates are repeatedly
ineligible (cooldown, post-release grace, or caps), but the scan
stays bounded:

- Per-pass list paging has a hard cap; no unbounded page walks.
- Per-pass candidate evaluation has a hard scan budget.
- Missing stays primary; cutoff and upgrade get separate budgets
  so one does not starve the others.

Backlog rotation improves without sacrificing polite API pacing.

## Existing instances after upgrade

Only fresh installs default to Random. Existing instances upgraded
from earlier Houndarr versions keep whatever setting they had
before (originally Chronological). Toggle to Random from the Edit
Instance form when you want to switch.

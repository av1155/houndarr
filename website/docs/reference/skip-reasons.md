---
sidebar_position: 3
title: Skip Reasons
description: What each skip reason means in the Houndarr search log, and when skips are normal.
---

# Skip Reasons

Every item Houndarr evaluates but does not search gets an
`action=skipped` row in the log with a reason string attached. Most
reasons are normal scheduling behavior, not errors.

## Reasons

| Reason string                         | Scope       | What it means                                                                                        |
| ------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------- |
| `on cooldown (Nd)`                    | per-item    | Missing item was searched less than `Cooldown (days)` ago.                                           |
| `on cutoff cooldown (Nd)`             | per-item    | Cutoff item was searched less than `Cutoff Cooldown` ago.                                            |
| `on upgrade cooldown (Nd)`            | per-item    | Upgrade item was searched less than `Upgrade Cooldown (days)` ago. Default 90 days.                  |
| `not yet released`                    | per-item    | The release date is in the future. An item with no release date counts as released.                  |
| `radarr reports not available`        | per-item    | Radarr's own availability flag says the movie is not available yet.                                  |
| `radarr status indicates unreleased`  | per-item    | Radarr's movie status is `tba` or `announced` and it is not flagged available.                       |
| `whisparr v3 reports not available`   | per-item    | Whisparr v3's availability flag says the scene is not available yet.                                 |
| `whisparr v3 status indicates unreleased` | per-item | Whisparr v3's status is `tba` or `announced` and it is not flagged available.                        |
| `future title not yet available`      | per-item    | The Radarr or Whisparr v3 release year is still ahead, the status is not released, and it is not flagged available. |
| `no series linked`                    | per-item    | Whisparr v2 returned an episode with no series attached, so it cannot be searched.                   |
| `post-release grace (Nh)`             | per-item    | Release date passed but the grace window (default 6 hours) has not elapsed.                          |
| `waiting on post-release grace (Nh)`  | per-item    | Season, artist, or author parent holding its early retry until a wanted item's grace window has certainly passed. |
| `in hot retry window (Nh)`            | per-item    | Missing item is inside its hot retry window, but the retry interval has not elapsed.                 |
| `hourly limit reached (N/hr)`         | per-item    | Missing pass hit `Hourly Cap` of `N` for the current hour.                                           |
| `cutoff hourly limit reached (N/hr)`  | per-item    | Cutoff pass hit `Cutoff Cap` of `N`.                                                                 |
| `upgrade hourly limit reached (N/hr)` | per-item    | Upgrade pass hit `Upgrade Cap` of `N`.                                                               |
| `tag filter (no included tag)`        | per-item    | `Tag Filter · Include` is set and the item does not carry any matching tag.                          |
| `tag filter (excluded tag)`           | per-item    | `Tag Filter · Exclude` is set and the item carries one of those tags.                                |
| `already in download queue`           | per-item    | The \*arr already has this item in its download queue (downloading, importing, or delayed).          |
| `queue backpressure (N/M)`            | cycle-level | Download queue has `N` items, at or above `Queue Limit` of `M`. Entire cycle is skipped.             |
| `outside allowed time window`         | cycle-level | Current time falls outside every window defined in `Allowed Search Window`. Entire cycle is skipped. |

Cycle-level skips write one log row and the supervisor sleeps until
the next cycle. Per-item skips write one row per candidate evaluated.

## Release-aware retry

With the default hot retry window of `0`, missing items skipped with
`not yet released` or `post-release grace (Nh)` get one immediate
retry on a later cycle once the release-timing gate clears, even when
the normal missing cooldown has not fully elapsed. After that one retry,
normal missing cooldown applies again.

When `Hot Retry Window (hrs)` is enabled, the latest `post-release grace
(Nh)` row opens a short retry window. Houndarr can retry the item after
`Hot Retry Interval (hrs)` elapses, still respecting batch size and the
hourly cap. When the window closes, normal missing cooldown applies, except
that an item the window never searched still takes its one retry.

Only dispatches and the release gate's own skips decide this, and a
dispatch counts whether it succeeded or errored. A skip written by any
other gate, such as `hourly limit reached (N/hr)`, a cooldown row or a
tag filter row, leaves the retry pending.

In season, artist, and author search mode these rows are logged under
the parent, so the parent holds its early retry until every
`post-release grace (Nh)` skip logged since its last search has
certainly passed, and logs `waiting on post-release grace (Nh)` while
it does. That keeps a just-aired episode from putting its whole season
back in the search queue on every cycle while it waits out its own
grace. `Run Now` searches anyway. Season 0 specials, and
items whose parent the \*arr did not report, are searched on their own
id and never wait on a parent.

A `not yet released` row in these modes is handled differently. Sonarr,
Whisparr v2, Lidarr and Readarr keep unreleased items out of their
wanted lists, so one reaches Houndarr only while this host's clock
trails the \*arr's, and it says nothing about the parent. When another
item of that parent passed the same release check on the cycle, the row
is dropped rather than logged against the parent, whether or not that
item went on to be searched. A parent with no item past the check on
that cycle still logs the row, and still takes its early retry once one
of them is released.

Each item in grace logs a row on every cycle that reaches it, so the
wait ends about one grace window after the last such row. A parent
whose items keep entering grace closer than about two grace windows
apart can stop taking the early retry altogether and fall back to its
ordinary cooldown; a daily show with `Post-Release Grace (hrs)` at
`18` sits in that range. A cycle reaches any one item less often on a
large library, which shortens the wait. While a wait is running, a
`Hot Retry Window (hrs)` shorter than `Post-Release Grace (hrs)`
closes before the wait ends, so set it longer if you want hot retries
to land.

Cutoff and upgrade passes do not use this early retry. They always
wait for their full cooldown.

## Already in download queue

Right before sending a search, Houndarr checks the \*arr's download
queue and skips any item that already has an entry there: downloading,
waiting to import, stuck on an import problem, waiting for a download
client, or held back by a delay profile. Searching again mostly spends
indexer hits, since the \*arr turns down another grab for a queued item
unless the quality profile allows upgrades and the search finds a
better release, and a search sent by Houndarr would skip the delay
profile entirely. No cooldown is recorded, so the item is searched
again on a later cycle once its entry leaves the queue. An entry that
never leaves, such as a stalled download, holds the item until you
clear it in the \*arr.

The queue is read at most once per cycle, and only when the cycle is
about to search something. In season, artist, or author search mode, a
queued item doesn't hold back the rest: the parent is skipped only when
every one of its wanted items the cycle reaches is already queued. If
the queue can't be read, the cycle searches as usual, writes a warning
to the container log, and logs a `download queue check (fetch failed)`
info row. A reverse proxy rule or an ACL can block that one endpoint
while every other request keeps working, so the check stays off cycle
after cycle and the row is what shows it on the Logs page. The row is
throttled to one per instance every six hours, and the window is held
in memory, so a restart starts it over.

## Queue backpressure

Setting `Queue Limit` to a value above zero makes Houndarr check the
download queue before each cycle. When the queue count meets or
exceeds the limit, the cycle writes one `queue backpressure (N/M)`
skip and sleeps. If the queue endpoint is unreachable, the cycle
proceeds normally (fail-open).

## Outside allowed time window

The `Allowed Search Window` field restricts scheduled cycles to one
or more time-of-day windows. When the current container-local time
falls outside every configured window, the cycle writes one
`outside allowed time window` info row with the current time and the
configured windows, then sleeps. Manual `Run Now` clicks bypass this
gate.

## Tag filter

`Tag Filter · Include` and `Tag Filter · Exclude` in instance
settings scope the missing, cutoff, and upgrade passes to (or away
from) items carrying specific *arr tags. Both fields take
comma-separated tag labels and default to empty. With both empty the
filter is a no-op and behavior matches earlier versions.

The engine resolves labels to numeric tag IDs once per cycle by
GET-ing each instance's `/tag` endpoint, so renaming a tag in Radarr
or Sonarr does not require re-editing the field. Two cycle-level info
rows can appear:

- `tag filter (unknown label)` lists labels the operator typed that
  did not resolve to any *arr tag on the current cycle. The remaining
  labels still apply.
- `tag filter (fetch failed)` indicates the `/tag` GET failed for
  that one cycle. The filter is disabled for that cycle and the
  search pass proceeds normally; the next cycle retries the fetch.

See [Instance Settings > Tag filter](/docs/reference/instance-settings#tag-filter)
for the field reference and the per-app tag-source mapping.

## Log deduplication

Eight reasons are deduplicated in the log: `on cooldown`, `on cutoff
cooldown`, `on upgrade cooldown`, `in hot retry window`, `waiting on
post-release grace`, `already in download queue`, and the two
`tag filter` skip reasons. Each
`(instance, item, reason)` triple writes at most one `search_log` row
per search pass every 24 hours on scheduled cycles. `Run now` always
writes its rows, and the window is held in memory, so a restart starts
it over. The engine still evaluates every candidate every cycle; only
the log write is suppressed. This keeps the logs scannable when
hundreds of items share the same cooldown, the same hot-retry interval
throttle, or the same tag-filter outcome.

The other reasons in the table above write a row every cycle they
apply.

## Why skips are normal

A high skip count with zero errors is pacing working as designed.
The engine evaluates candidates, finds most ineligible, and waits.

Worked example: 500 monitored movies, 50 flagged cutoff-unmet, 35 of
those on cooldown, 8 inside post-release grace, batch size 1. The
cycle searches 1 movie and skips 49. Over days and weeks the engine
works through the backlog as cooldowns expire and grace windows
close.

Errors (HTTP 401, connection refused) are the real signal that
something is wrong. See
[Troubleshoot Connection](/docs/guides/troubleshoot-connection)
when errors appear.

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

| Reason string | Scope | What it means |
|---------------|-------|---------------|
| `on cooldown (Nd)` | per-item | Missing item was searched less than `Cooldown (days)` ago. |
| `on cutoff cooldown (Nd)` | per-item | Cutoff item was searched less than `Cutoff Cooldown` ago. |
| `on upgrade cooldown (Nd)` | per-item | Upgrade item was searched less than `Upgrade Cooldown (days)` ago. Default 90 days. |
| `not yet released` | per-item | No release date, or the release date is in the future. |
| `post-release grace (Nh)` | per-item | Release date passed but the grace window (default 6 hours) has not elapsed. |
| `hourly cap reached (N)` | per-item | Missing pass hit `Hourly Cap` of `N` for the current hour. |
| `cutoff hourly cap reached (N)` | per-item | Cutoff pass hit `Cutoff Cap` of `N`. |
| `upgrade hourly cap reached (N)` | per-item | Upgrade pass hit `Upgrade Cap` of `N`. |
| `queue backpressure (N/M)` | cycle-level | Download queue has `N` items, at or above `Queue Limit` of `M`. Entire cycle is skipped. |
| `outside allowed time window` | cycle-level | Current time falls outside every window defined in `Allowed Search Window`. Entire cycle is skipped. |

Cycle-level skips write one log row and the supervisor sleeps until
the next cycle. Per-item skips write one row per candidate evaluated.

## Release-aware retry

Missing items skipped with `not yet released` or `post-release grace
(Nh)` get one immediate retry on a later cycle once the release-timing
gate clears, even when the normal missing cooldown has not fully
elapsed. After that one retry, normal missing cooldown applies again.

Cutoff and upgrade passes do not use this early retry. They always
wait for their full cooldown.

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

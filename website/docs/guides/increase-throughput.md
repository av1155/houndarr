---
sidebar_position: 6
title: Increase Throughput
description: How to clear a large backlog faster without overwhelming your indexers or earning a ban.
---

# Increase Throughput

The default profile (batch size 2, hourly cap 4, cooldown 14 days)
clears roughly 4 to 8 searches per day per instance. A backlog of
several hundred items takes weeks at those defaults.

Raising throughput is safe when you do it one knob at a time and
watch the Logs page. Indexers hand out rate limits and bans fast
when hit with bursts; the defaults exist so you do not earn one.

## Order of adjustments

Tune in this order. After each change, watch the Logs page for a
full day before moving to the next step.

1. **Raise Batch Size** from 2 to 3 or 4. More items per cycle
   clears the backlog faster without any change to frequency.
2. **Lower Sleep (minutes)** from 30 to 20 or 15. Cycles run more
   often. Watch for indexer errors.
3. **Raise Hourly Cap** from 4 to 6 or 8. Only move this if
   indexers remain healthy after the first two changes.
4. **Enable Cutoff search** once the missing backlog is under
   control. Cutoff defaults (batch 1, cap 1, cooldown 21 days) are
   intentionally more cautious because quality upgrades matter
   less than acquiring missing content. Quality profile tuning
   lives in [TRaSH-Guides](https://trash-guides.info/) if you need
   help deciding what to mark cutoff-unmet.
5. **Enable Upgrade search** last, only after both missing and
   cutoff backlogs are stable. Upgrade defaults (batch 1 hard-
   capped at 5, cap 1 hard-capped at 5, cooldown 90 days with a
   7-day floor) cannot be loosened past the engine-enforced hard
   caps.

## Common mistakes

- Raising two knobs at once. When indexer errors show up, you
  cannot tell which knob caused them.
- Setting `Hourly Cap = 0` without knowing your indexer's own
  per-hour limit. If your indexer caps you at 50 searches per day,
  disabling Houndarr's cap buys you nothing.
- Expecting a big backlog to clear in days. 500 cutoff-unmet movies
  at cap 1 is roughly 24 searches per day. Even at cap 4, that is
  a week or more. Plan accordingly.

## When to stop tuning

Stop at the first profile where:

- Logs show zero errors across a 7-day window.
- Your *arr instance's Activity or History shows consistent grabs
  for items you expect to be available.
- Your indexers stay healthy (no HTTP 429s, no bans, no account
  suspensions).

Beyond that, more aggressive tuning risks a ban faster than it
clears the backlog.

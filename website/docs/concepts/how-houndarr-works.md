---
sidebar_position: 1
title: How Houndarr Works
description: What Houndarr does, how it decides what to search, and why most items get skipped each cycle.
---

# How Houndarr Works

Houndarr is a search scheduler for Sonarr and Radarr. It triggers search commands in small, rate-limited batches so you don't have to hit "Search All Missing" and overwhelm your indexers.

It does not download anything, parse releases, evaluate quality, or replace Sonarr/Radarr. It only decides **when** to ask them to search and **how many** items to include per batch.

## The search cycle

```
1. Houndarr asks Sonarr/Radarr: "What items are missing or cutoff-unmet?"
       ↓
2. Sonarr/Radarr respond with their wanted lists
   (only monitored items that are missing or below quality cutoff)
       ↓
3. Houndarr applies its scheduling rules to each candidate
   (cooldown, hourly cap, unreleased delay, batch size)
       ↓
4. Eligible items → search command sent to Sonarr/Radarr
       ↓
5. Ineligible items → logged as "skipped", retried next cycle
```

Sonarr and Radarr do all the actual searching. Houndarr controls the pacing.

## Monitored vs. wanted

A **monitored** item in Sonarr/Radarr just means the software is tracking it. If the item is already downloaded at a quality that meets your cutoff, it won't appear in any wanted list, and Houndarr will never touch it.

| Item state in Sonarr/Radarr | Will Houndarr search it? |
|-----------------------------|--------------------------|
| Monitored + missing | Yes, if eligible under scheduling rules |
| Monitored + downloaded + cutoff met | **No** — not in any wanted list |
| Monitored + downloaded + cutoff unmet | Yes (if cutoff search is enabled), if eligible |
| Not monitored | **No** — Houndarr only reads monitored wanted lists |

## Who decides "cutoff unmet"?

Sonarr and Radarr — not Houndarr. They populate the `wanted/cutoff` API list based on your quality profile settings. Houndarr reads that list and applies its scheduling rules on top.

If cutoff searches aren't happening, check whether the item actually appears in Sonarr/Radarr's own "Wanted > Cutoff Unmet" view first.

:::tip Quality profiles are managed in Sonarr/Radarr — not Houndarr
Houndarr works best when your Sonarr/Radarr instances are already configured with
quality profiles you trust. It does not manage quality profiles or custom formats.

If you manage multiple instances or want help keeping quality settings consistent,
community tools such as [Profilarr](https://github.com/Dictionarry-Hub/profilarr)
can sync profiles and custom formats across instances. These tools are optional and
fully independent of Houndarr.
:::

## Why only a few items get searched each cycle

Think of it as a funnel:

```
Your monitored library
        │
        │  Sonarr/Radarr filter: only missing or cutoff-unmet items
        ▼
  Wanted list (much smaller)
        │
        │  Houndarr filter: cooldown, unreleased delay, hourly cap
        ▼
  Eligible this cycle (smaller still)
        │
        │  Batch size limit
        ▼
  Actually searched (often just 1–3 items)
```

For example, if you have 500 monitored movies in Radarr but only 50 are cutoff-unmet, and 35 of those are on cooldown, 8 are unreleased, and your batch is 1 — Houndarr searches 1 movie that cycle. The rest wait for cooldowns to expire or release windows to pass, and Houndarr works through them over days and weeks.

## The two search passes

Each enabled instance runs two independent passes:

| Pass | What it searches | Key controls |
|------|-----------------|--------------|
| **Missing** | Items in Sonarr/Radarr's `wanted/missing` list | Batch size, sleep interval, hourly cap, cooldown, unreleased delay |
| **Cutoff** | Items in Sonarr/Radarr's `wanted/cutoff` list | Cutoff batch, cutoff cap, cutoff cooldown |

Cutoff search is **off by default**. Enable it only after missing items are under control so the two passes don't compete for the same indexer budget.

## What "skipped" means in the logs

Every item Houndarr considers but does not search is logged as `skipped` with a reason:

| Reason in logs | What it means |
|----------------|---------------|
| `cooldown (N days remaining)` | Item was searched recently; waiting to retry |
| `unreleased delay (N hrs remaining)` | Release date is too recent or in the future |
| `hourly cap reached` | This instance has hit its per-hour search limit |

A high skip count with zero errors means Houndarr is pacing itself correctly — examining candidates, finding most ineligible under your rules, and waiting patiently.

See [FAQ](/docs/concepts/faq) for answers to specific questions, and [Troubleshooting](/docs/concepts/troubleshooting) if you want to verify everything is connected and running.

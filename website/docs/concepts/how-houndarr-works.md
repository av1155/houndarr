---
sidebar_position: 1
title: How Houndarr Works
description: A plain-English explanation of what Houndarr does, how it decides what to search, and why low search counts are normal.
---

# How Houndarr Works

Understanding Houndarr's mental model will save you hours of confusion. This page explains what Houndarr does, what it does not do, and why seeing many skips and few searches is usually a sign that everything is working correctly.

## What Houndarr is

Houndarr is a **scheduler and orchestrator**. Its job is to trigger search commands in Sonarr and Radarr in a slow, polite, and automatic way — preventing the indexer overload that happens when you hit "Search All Missing" manually.

It is:

- A lightweight companion that sits alongside your existing Sonarr/Radarr stack.
- A rate-limited search scheduler with per-item cooldowns, hourly caps, and configurable batch sizes.
- A tool that gently works through your backlog over time.

It is **not**:

- A downloader — it does not fetch files itself.
- An indexer or release parser — it has no knowledge of what releases exist.
- A quality decision engine — it does not decide whether a file meets your cutoff.
- A Sonarr/Radarr replacement — it only triggers searches that Sonarr/Radarr then handle.
- A tool that blindly searches every monitored item in your library.

## How it works — step by step

```
1. Houndarr asks Sonarr/Radarr: "What items are missing or cutoff-unmet?"
       ↓
2. Sonarr/Radarr respond with their wanted lists
   (only monitored items that are missing or below quality cutoff)
       ↓
3. Houndarr reviews each candidate and applies its scheduling rules
   (cooldown, hourly cap, unreleased delay, batch size)
       ↓
4. Eligible items → search command sent to Sonarr/Radarr
       ↓
5. Ineligible items → logged as "skipped", retried next cycle
```

Sonarr and Radarr do all the actual searching. Houndarr only decides **when** to ask them to search, and **how many** items to ask about at once.

## "Monitored" does not mean "will be searched"

This is the most common source of confusion.

A **monitored** item in Sonarr or Radarr simply means you want the software to track it. It does **not** mean:

- The item is missing.
- The item is below your quality cutoff.
- Houndarr will search for it.

Houndarr only acts on items that Sonarr or Radarr **report as wanted** — that is, items that appear in their missing or cutoff-unmet lists. If an item is monitored but already downloaded at a quality that meets your cutoff, Sonarr/Radarr will not include it in either wanted list, and Houndarr will never touch it.

| Item state in Sonarr/Radarr | Will Houndarr search it? |
|-----------------------------|--------------------------|
| Monitored + missing | Yes, if eligible under scheduling rules |
| Monitored + downloaded + cutoff met | **No** — not in any wanted list |
| Monitored + downloaded + cutoff unmet | Yes (if cutoff search is enabled), if eligible |
| Not monitored | **No** — Houndarr only reads monitored wanted lists |

## Who decides "cutoff unmet"?

**Sonarr and Radarr decide this — not Houndarr.**

Houndarr reads the `wanted/cutoff` API endpoint from each instance. Sonarr and Radarr populate that list based on your quality profile settings: if the file you have does not meet the profile's cutoff quality, the item appears there. Houndarr simply reads that list and applies its own scheduling rules on top.

If you are not seeing cutoff searches happen, the first thing to check is whether the item actually appears in Sonarr/Radarr's own "Wanted → Cutoff Unmet" view.

:::tip Quality profiles are managed in Sonarr/Radarr — not Houndarr
Houndarr works best when your Sonarr/Radarr instances are already configured with
quality profiles you trust. It does not manage quality profiles or custom formats.

If you manage multiple instances or want help keeping quality settings consistent,
community tools such as [Profilarr](https://github.com/Dictionarry-Hub/profilarr)
can sync profiles and custom formats across instances. These tools are optional and
fully independent of Houndarr.
:::

## The search funnel — why your search count is small

Think of it as a funnel. At each stage, items are filtered out:

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

### Worked example

Say you have:

- **500 monitored movies** in Radarr
- **50** are cutoff-unmet (Radarr's wanted/cutoff list)
- **35** of those 50 were searched recently and are on a **21-day cooldown**
- **8** of the remaining 15 are future/unreleased films inside the **36-hour unreleased delay**
- **7** are eligible — but your **hourly cap is 1** and **batch is 1**

In a single pass, Houndarr searches **1 movie**. The other 499 are either not in the wanted list, on cooldown, or skipped for another reason.

**This is expected. This does not mean Houndarr is stuck.**

Over days and weeks, as cooldowns expire and releases become available, Houndarr steadily works through the eligible items. Conservative defaults trade aggressiveness for indexer politeness — which is the entire point.

## The two search passes

Each enabled instance runs two independent passes on a configurable schedule:

| Pass | What it searches | Key controls |
|------|-----------------|--------------|
| **Missing** | Items in Sonarr/Radarr's `wanted/missing` list | Batch size, sleep interval, hourly cap, cooldown, unreleased delay |
| **Cutoff** | Items in Sonarr/Radarr's `wanted/cutoff` list | Cutoff batch, cutoff cap, cutoff cooldown |

Cutoff search is **off by default**. Enable it only after missing items are under control, so the two passes do not compete for the same indexer budget.

## What "skipped" means in the logs

Every item Houndarr considers but does not search is logged with an action of `skipped` and a reason. Common reasons:

| Reason in logs | What it means |
|----------------|---------------|
| `cooldown (N days remaining)` | Item was searched recently; waiting to retry |
| `unreleased delay (N hrs remaining)` | Release date is too recent or in the future |
| `hourly cap reached` | This instance has hit its per-hour search limit |
| `not in wanted list` | Item is monitored but Sonarr/Radarr do not report it as missing or cutoff-unmet |

A high skip count with zero errors is a **strong signal of health**. It means Houndarr is examining candidates, finding most of them ineligible under your rules, and waiting patiently for them to become eligible.

## Summary

- Houndarr reads Sonarr/Radarr wanted lists — it does not scan your whole library.
- Sonarr/Radarr decide what is missing and what is cutoff-unmet.
- Houndarr adds a scheduling layer: cooldowns, caps, delays, and batch limits.
- Low search counts and many skips are normal and expected.
- A high skip count with zero errors means Houndarr is working correctly.

See [FAQ](/docs/concepts/faq) for answers to specific questions, and [Troubleshooting](/docs/concepts/troubleshooting) for steps to verify Houndarr is working as expected.

---
sidebar_position: 2
title: Instance Settings
description: Detailed guide to all per-instance search settings in Houndarr.
---

# Instance Settings

This guide explains each setting available when adding or editing a Sonarr/Radarr
instance in Houndarr. The defaults are conservative — keep settings low to reduce
indexer/API pressure and avoid bans.

## Search command contract

- **Sonarr (default):** Sends episode-level commands (`EpisodeSearch` with `episodeIds`).
- **Sonarr (advanced):** Missing pass can use season-context commands
  (`SeasonSearch` with `seriesId` + `seasonNumber`) when enabled per instance.
- **Radarr:** Sends movie-level commands (`MoviesSearch` with `movieIds`).
- Wanted-list reads are restricted to monitored items (`monitored=true`) for both
  missing and cutoff passes.

## Missing search controls

### Batch Size

Maximum number of missing items considered per cycle.

- **Default:** `2`
- Lower values are safer; higher values clear backlog faster.

### Sleep (minutes)

Wait time between cycles for each enabled instance.

- **Default:** `30`
- Lower values increase request frequency.

### Hourly Cap

Maximum successful missing searches per hour.

- **Default:** `4`
- Set `0` to disable this cap (not recommended unless you trust upstream limits).

### Cooldown (days)

Minimum days before retrying the same missing item.

- **Default:** `14`
- Larger values reduce repeat search noise.

### Unreleased Delay (hours)

Minimum delay after release date before searching.

- **Default:** `36`
- If the item is still inside this window, Houndarr logs `unreleased delay (...)` and skips it.

For Radarr, Houndarr evaluates release timing with fallback anchors in this order:
`digitalRelease` → `physicalRelease` → `releaseDate` → `inCinemas`.

For Radarr, unavailable or clearly pre-release titles may also be skipped using
availability signals (`isAvailable` / `status`) even when release dates are incomplete.

### Sonarr Missing Search Mode

Strategy for Sonarr missing-pass commands.

- **Default:** `Episode search (default)`
- **Advanced:** `Season-context search (advanced)`

Season-context mode sends at most one `SeasonSearch` per `(series, season)` per pass.
Season search is not pack-only in Sonarr and may still produce singles or noisier behavior.

:::info
Cooldown in season-context mode is tracked through the representative missing episode
that triggered that season search.
:::

## Cutoff upgrade controls

### Cutoff search

Enable searching for items that do not meet your quality cutoff.

- **Default:** Off
- Keep this off unless missing items are already under control.

### Cutoff Batch

Maximum cutoff items considered per cutoff cycle.

- **Default:** `1`

### Cutoff Cooldown

Minimum days before retrying the same cutoff item.

- **Default:** `21`

### Cutoff Cap

Maximum successful cutoff searches per hour.

- **Default:** `1`
- Set `0` to disable cutoff hourly cap.

Cutoff searches use separate cap/cooldown settings from missing searches so they
do not consume the same budget.

## Fair backlog scanning

Houndarr does not stop at the first wanted page. During each cycle, it can scan
deeper pages when top candidates are repeatedly ineligible (cooldown, unreleased
delay, or caps), but it stays bounded:

- Per-pass list paging has a hard cap (no unbounded page walks)
- Per-pass candidate evaluation has a hard scan budget
- Missing remains primary; cutoff remains separate and conservative

This improves backlog rotation while preserving polite API behavior.

## Recommended starting profile

| Setting | Value |
|---------|-------|
| Batch Size | `2` |
| Sleep (minutes) | `30` |
| Hourly Cap | `4` |
| Cooldown (days) | `14` |
| Unreleased Delay (hrs) | `36` |
| Cutoff search | Off |
| Cutoff Batch | `1` |
| Cutoff Cooldown | `21` |
| Cutoff Cap | `1` |

## Increasing throughput

Increase one control at a time and observe logs for a full day.

Suggested order:

1. Increase **Batch Size** slightly.
2. Lower **Sleep (minutes)** slightly.
3. Increase **Hourly Cap** only if indexers remain healthy.
4. Enable **Cutoff search** last.

## Status control

Instance enabled/disabled state is controlled from the row toggle in Settings.
New instances are created as enabled by default.

![Settings](../../static/img/screenshots/Settings_Houndarr.jpeg)

# Houndarr Instance Settings Guide

This guide explains each Add/Edit Instance setting in Houndarr, with safe defaults and practical recommendations.

Houndarr is a polite backlog orchestrator. Keep settings conservative to reduce indexer/API pressure and avoid bans.

## Missing Search Controls

- **Batch Size**: maximum number of missing items considered per cycle.
  - Default: `2`
  - Lower values are safer; higher values clear backlog faster.

- **Sleep (minutes)**: wait time between cycles for each enabled instance.
  - Default: `30`
  - Lower values increase request frequency.

- **Hourly Cap**: maximum successful missing searches per hour.
  - Default: `4`
  - Set `0` to disable this cap (not recommended unless you trust upstream limits).

- **Cooldown (days)**: minimum days before retrying the same missing item.
  - Default: `14`
  - Larger values reduce repeat search noise.

- **Unreleased Delay (hrs)**: minimum delay after release date before searching.
  - Default: `36`
  - If the item is still inside this window, Houndarr logs `unreleased delay (...)` and skips it.

## Cutoff Upgrade Controls

- **Cutoff search**: enable searching for items that do not meet quality cutoff.
  - Default: Off
  - Keep this off unless missing items are already under control.

- **Cutoff Batch**: maximum cutoff items considered per cutoff cycle.
  - Default: `1`

- **Cutoff Cooldown**: minimum days before retrying the same cutoff item.
  - Default: `21`

- **Cutoff Cap**: maximum successful cutoff searches per hour.
  - Default: `1`
  - Set `0` to disable cutoff hourly cap.

Cutoff searches use separate cap/cooldown settings from missing searches so they do not consume the same budget.

## Status Control

- **Enabled/Disabled**: controlled from the row toggle in Settings.
  - Add/Edit modal no longer changes this state.
  - New instances are created as enabled by default.

## Recommended Starting Profile

- Batch Size: `2`
- Sleep (minutes): `30`
- Hourly Cap: `4`
- Cooldown (days): `14`
- Unreleased Delay (hrs): `36`
- Cutoff search: `Off`
- Cutoff Batch: `1`
- Cutoff Cooldown: `21`
- Cutoff Cap: `1`

## If You Need More Throughput

Increase one control at a time and observe logs for a full day.

Suggested order:
1. Increase **Batch Size** slightly.
2. Lower **Sleep (minutes)** slightly.
3. Increase **Hourly Cap** only if indexers remain healthy.
4. Enable **Cutoff search** last.

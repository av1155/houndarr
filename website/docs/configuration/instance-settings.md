---
sidebar_position: 2
title: Instance Settings
description: Detailed guide to all per-instance search settings in Houndarr.
---

# Instance Settings

This guide explains each setting available when adding or editing an instance
in Houndarr. The defaults are conservative; keep settings low to reduce
indexer/API pressure and avoid bans.

![Add instance form](../../static/img/screenshots/Settings_Houndarr_Add_Instance_Settings.jpeg)

## Search command contract

- **Radarr:** Sends movie-level commands (`MoviesSearch` with `movieIds`).
- **Sonarr (default):** Sends episode-level commands (`EpisodeSearch` with `episodeIds`).
- **Sonarr (advanced):** Missing pass can use season-context commands
  (`SeasonSearch` with `seriesId` + `seasonNumber`) when enabled per instance.
- **Lidarr (default):** Sends album-level commands (`AlbumSearch` with `albumIds`).
- **Lidarr (advanced):** Missing pass can use artist-context commands
  (`ArtistSearch` with `artistId`) when enabled per instance.
- **Readarr (default):** Sends book-level commands (`BookSearch` with `bookIds`).
- **Readarr (advanced):** Missing pass can use author-context commands
  (`AuthorSearch` with `authorId`) when enabled per instance.
- **Whisparr v2 (default):** Sends episode-level commands (`EpisodeSearch` with `episodeIds`).
- **Whisparr v2 (advanced):** Missing pass can use season-context commands
  (`SeasonSearch` with `seriesId` + `seasonNumber`) when enabled per instance.
- **Whisparr v3:** Sends movie-level commands (`MoviesSearch` with `movieIds`). No search mode options (always movie-level, like Radarr).
- Wanted-list reads are restricted to monitored items (`monitored=true`) for both
  missing and cutoff passes across all app types.
- **Upgrade pass:** Re-uses the same search commands as above but targets library
  items that already have files and meet cutoff. The upgrade pass reads the full
  library endpoint rather than the `wanted/*` APIs.

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
- Missing only: if the latest missing-pass skip for an item was `not yet released`
  or `post-release grace (Nh)`, Houndarr allows one retry as soon as the item
  becomes eligible, even if the normal missing cooldown has not fully elapsed.
- After that retry, normal missing cooldown resumes.

### Post-Release Grace (hours)

Hours to wait after an item's release date before searching.

- **Default:** `6`
- Items still within this window are logged as `post-release grace (Nh)` and skipped.
- Items that have not been released yet (no release date, or date in the future) are always skipped with reason `not yet released`, regardless of this setting.
- Once a missing item clears `not yet released` or `post-release grace (Nh)`,
  Houndarr may retry it on the next missing pass without waiting for the full
  missing cooldown.

Release date evaluation varies by app type:

- **Radarr:** Fallback anchors in order: `digitalRelease` → `physicalRelease` → `releaseDate` → `inCinemas`. Unavailable or pre-release titles may also be skipped using availability signals (`isAvailable` / `status`).
- **Sonarr / Whisparr v2:** Uses `airDateUtc` (Sonarr) or the `releaseDate` field (Whisparr v2).
- **Whisparr v3:** Same fallback chain as Radarr (`digitalRelease` -> `physicalRelease` -> `inCinemas`).
- **Lidarr:** Uses the album `releaseDate` field.
- **Readarr:** Uses the book `releaseDate` field.

### Sonarr Missing Search Mode

Strategy for Sonarr missing-pass commands.

- **Default:** `Episode search (default)`
- **Advanced:** `Season-context search (advanced)`

Season-context mode sends at most one `SeasonSearch` per `(series, season)` per pass.

:::info
Cooldown in season-context mode is tracked at the season level using a stable synthetic
identifier derived from the series ID and season number, not through any individual
episode. This ensures cooldown history is consistent across cycles regardless of which
episode happens to appear first on the wanted list.
:::

### Lidarr Missing Search Mode

Strategy for Lidarr missing-pass commands.

- **Default:** `Album search (default)`
- **Advanced:** `Artist-context search (advanced)`

Artist-context mode sends at most one `ArtistSearch` per artist per pass.

### Readarr Missing Search Mode

Strategy for Readarr missing-pass commands.

- **Default:** `Book search (default)`
- **Advanced:** `Author-context search (advanced)`

Author-context mode sends at most one `AuthorSearch` per author per pass.

### Whisparr v2 Missing Search Mode

Strategy for Whisparr v2 missing-pass commands (v3 has no search mode; it always searches at the movie level, like Radarr).

- **Default:** `Episode search (default)`
- **Advanced:** `Season-context search (advanced)`

Season-context mode sends at most one `SeasonSearch` per `(series, season)` per pass, same as Sonarr's season-context mode.

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

The release-aware retry above does not apply to cutoff searches.

## Library upgrade controls

### Upgrade search

Enable searching for items that already have files and meet your quality cutoff. This lets your *arr instance find better releases based on quality profiles and custom format scoring.

- **Default:** Off
- Keep this off unless both missing and cutoff backlogs are stable.
- Unlike cutoff search (which targets items *below* cutoff), upgrade search targets items that *already meet* cutoff.

### Upgrade Batch

Maximum upgrade items considered per cycle.

- **Default:** `1`
- **Hard cap:** `5`. The engine enforces this maximum regardless of the configured value.

### Upgrade Cooldown (days)

Minimum days before retrying the same upgrade item.

- **Default:** `90`
- **Minimum:** `7`. The engine enforces this floor.
- Much longer than missing or cutoff cooldowns because upgrades are lowest priority.

### Upgrade Cap

Maximum successful upgrade searches per hour.

- **Default:** `1`
- **Hard cap:** `5`. The engine enforces this maximum.
- Set `0` to disable upgrade hourly cap.

### Upgrade Search Mode

Per-app strategy for upgrade-pass search commands. Each app type (Sonarr, Lidarr, Readarr, Whisparr) has its own upgrade search mode, independent of the missing search mode. Radarr always searches at the movie level.

- **Sonarr/Whisparr v2:** Episode (default) or Season-context
- **Lidarr:** Album (default) or Artist-context
- **Readarr:** Book (default) or Author-context
- **Radarr/Whisparr v3:** Always movie-level (no mode selection)

### Offset-based rotation

The upgrade pass uses a persistent offset to rotate through your library over time rather than always starting from the beginning. This ensures fair coverage across your entire library. Offsets reset to zero when upgrade search is toggled off.

The missing and cutoff passes also use page-based rotation. Each pass remembers which API page it reached and starts from there on the next cycle. This prevents items further down the list from being starved when earlier items are all on cooldown. Offsets reset to page 1 when you save instance settings.

## Queue backpressure

When `Queue Limit` is set above zero, Houndarr checks the instance's download
queue before each cycle. If the total queue count meets or exceeds the limit,
the entire cycle is skipped and logged as `queue backpressure (N/M)`.

- **Default:** `0` (disabled)
- If the queue endpoint is unreachable, the search proceeds normally (fails open).
- This prevents Houndarr from piling up work when the download client is already busy.

## Allowed search window

The `Allowed Search Window` field restricts scheduled cycles to one or more
time-of-day windows. Use it when your NAS or host puts disks to sleep during
off-hours and you do not want Houndarr waking them up.

**Format:** `HH:MM-HH:MM` per window, comma-separated for multiple windows.

- `09:00-23:00`: allow searches from 9 AM up to (but not including) 11 PM.
- `09:00-12:00,18:00-22:00`: two separate windows in one day.
- `22:00-06:00`: wrap-around window covering late night through early morning.
- Leave blank for 24/7 operation.

**Timezone:** Windows are interpreted in the container's local time, which
follows the `TZ` environment variable (e.g. `TZ=America/New_York`). If `TZ`
is unset, Houndarr falls back to UTC. In zones with daylight-saving
transitions, avoid windows that overlap the spring-forward gap (02:00-03:00
does not exist that day) or the fall-back repeat (01:00-02:00 occurs twice);
configure windows outside those hours if predictability matters.

**Boundary semantics:** Start is inclusive, end is exclusive. `09:00-12:00`
allows searches at 09:00:00 but blocks them at 12:00:00.

**Run Now bypass:** Manual `Run Now` clicks always run, even outside the
window. The window is an operator-preference schedule, not a safety gate;
queue backpressure and hourly caps still apply to manual runs.

**When skipped:** The cycle writes a single `info` row to the search log with
reason `outside allowed time window` and a message showing the current local
time next to the configured window. The supervisor sleeps normally and
re-checks on the next cycle, so operators who want faster re-entry can
lower `Sleep (minutes)`.

- **Default:** empty (24/7, no gate)

## Fair backlog scanning

Houndarr does not stop at the first wanted page. During each cycle, it can scan
deeper pages when top candidates are repeatedly ineligible (cooldown, post-release
grace, or caps), but it stays bounded:

- Per-pass list paging has a hard cap (no unbounded page walks)
- Per-pass candidate evaluation has a hard scan budget
- Missing remains primary; cutoff remains separate and conservative

This improves backlog rotation while preserving polite API behavior.

:::tip Why am I seeing mostly skips?
Skips are normal. See [How Houndarr Works](/docs/concepts/how-houndarr-works#what-skipped-means-in-the-logs) and the [FAQ](/docs/concepts/faq) for details.
:::

## Recommended starting profile

| Setting | Value |
|---------|-------|
| Batch Size | `2` |
| Sleep (minutes) | `30` |
| Hourly Cap | `4` |
| Cooldown (days) | `14` |
| Post-Release Grace (hrs) | `6` |
| Queue Limit | `0` (disabled) |
| Allowed Search Window | (blank, 24/7) |
| Cutoff search | Off |
| Cutoff Batch | `1` |
| Cutoff Cooldown | `21` |
| Cutoff Cap | `1` |
| Upgrade search | Off |
| Upgrade Batch | `1` (hard cap: 5) |
| Upgrade Cooldown | `90` (min: 7) |
| Upgrade Cap | `1` (hard cap: 5) |

## Increasing throughput

Increase one control at a time and observe logs for a full day.

Suggested order:

1. Increase **Batch Size** slightly.
2. Lower **Sleep (minutes)** slightly.
3. Increase **Hourly Cap** only if indexers remain healthy.
4. Enable **Cutoff search** after missing backlog is under control.
5. Enable **Upgrade search** last, only after both missing and cutoff are stable.

## Status control

Instance enabled/disabled state is controlled from the row toggle in Settings.
New instances are created as enabled by default.

![Settings](../../static/img/screenshots/Settings_Houndarr.jpeg)

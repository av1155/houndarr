---
sidebar_position: 2
title: Instance Settings
description: Reference for every per-instance search setting in Houndarr.
---

import Image from '@theme/IdealImage';

# Instance Settings

Per-instance configuration for the search engine. Each field is
documented with default, range where applicable, and behavior.

For the Search Order explanation (Random vs Chronological) see
[Search Order](/docs/concepts/search-order). For tuning guidance
see [Increase Throughput](/docs/guides/increase-throughput). For
the per-app API command Houndarr sends see
[Search Commands](/docs/reference/search-commands).

:::tip Want to start fresh?
If you want every instance back to default policy without losing the
instances themselves, use **Settings > Admin > Maintenance > Reset
all instance settings**. It reverts every field below at once except
the tag filters and the enabled toggle, and keeps your connection
details and API keys. See
[Admin actions](/docs/guides/admin-actions) for the full list of
what a reset touches.
:::

<Image
img={require('@site/static/img/screenshots/houndarr-add-instance-form.png')}
alt="The Houndarr Add Instance form showing search, cutoff, and upgrade configuration fields"
/>

The same form opens when you click **Edit** on an existing row, with
the current values pre-populated. The API key stays masked until you
overwrite it.

<Image
img={require('@site/static/img/screenshots/houndarr-edit-instance-form.png')}
alt="The Houndarr Edit Instance modal, pre-populated with an existing Sonarr instance's connection, search policy, and schedule values"
/>

## Missing search controls

### Missing search

Turns the missing-item pass on or off for this instance. Turn it off
to pause missing searches without changing the settings below.

- Default: On

### Batch Size

Maximum number of missing items considered per cycle.

- Default: `2`
- Range: `1` to `250`
- Lower values are safer; higher values clear backlog faster.

### Sleep (minutes)

Wait time between cycles for each enabled instance.

- Default: `30`
- Range: `1` to `525600` (one year)
- Lower values increase request frequency.

### Hourly Cap

Maximum successful missing searches per hour.

- Default: `4`
- Range: `0` to `10000`
- Set `0` to disable this cap (not recommended unless you trust
  upstream limits).

### Cooldown (days)

Minimum days before retrying the same missing item.

- Default: `14`
- Range: `0` to `3650` (ten years)
- Larger values reduce repeat search noise.
- Release-aware retry applies to the missing pass only. See
  [Skip Reasons](/docs/reference/skip-reasons#release-aware-retry).

### Post-Release Grace (hours)

Hours to wait after an item's release date before searching.

- Default: `6`
- Range: `0` to `8760` (one year)
- Items inside this window log as `post-release grace (Nh)` and
  skip.
- Items with a release date in the future log as `not yet released`
  regardless of this setting. An item with no release date counts as
  released.

### Hot Retry Window (hours)

Optional hours after post-release grace when a missing item can retry
on a short interval before the normal missing cooldown takes over.

- Default: `0` (disabled)
- Range: `0` to `8760`
- The latest `post-release grace (Nh)` skip opens the window.
- Hot retries still count against Batch Size and Hourly Cap.

### Hot Retry Interval (hours)

Minimum hours between hot retry attempts while the hot retry window is
active.

- Default: `2`
- Maximum: `8760`
- Must be at least `1` when Hot Retry Window is enabled.
- Manual Run Now bypasses the interval, but it does not reopen an expired window.

Release date evaluation varies by app type:

| App                 | Date priority chain                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Radarr              | `digitalRelease` -> `physicalRelease` -> `releaseDate` -> `inCinemas`. Unavailable or pre-release titles may be skipped via `isAvailable` / `status`. |
| Sonarr, Whisparr v2 | `airDateUtc` (Sonarr) or `releaseDate` (Whisparr v2)                                                                                                  |
| Whisparr v3         | Same chain as Radarr: `digitalRelease` -> `physicalRelease` -> `releaseDate` -> `inCinemas`                                                           |
| Lidarr              | Album `releaseDate`                                                                                                                                   |
| Readarr             | Book `releaseDate`                                                                                                                                    |

### Sonarr Missing Search Mode

Strategy for Sonarr missing-pass commands.

- Default: `Episode search (default)`
- Advanced: `Season-context search (advanced)`

Season-context mode sends at most one `SeasonSearch` per
`(series, season)` per pass. Cooldown in season-context mode is
tracked at the season level via a stable synthetic ID derived from
the series ID and season number, not through any individual
episode. Cooldown history stays consistent across cycles regardless
of which episode appears first on the wanted list.

### Lidarr Missing Search Mode

- Default: `Album search (default)`
- Advanced: `Artist-context search (advanced)`

Artist-context mode sends at most one `ArtistSearch` per artist per
pass.

### Readarr Missing Search Mode

- Default: `Book search (default)`
- Advanced: `Author-context search (advanced)`

Author-context mode sends at most one `AuthorSearch` per author per
pass.

### Whisparr v2 Missing Search Mode

- Default: `Episode search (default)`
- Advanced: `Season-context search (advanced)`

Whisparr v3 has no search mode selection; it always searches at the
movie level.

## Tag filter

Scope a per-instance search to (or away from) items carrying specific
*arr tags. The two fields below apply to the missing, cutoff, and
upgrade passes alike and are empty by default. Each pass can also skip
tags of its own; see [Per-pass exclude](#per-pass-exclude).

### Tag Filter · Include

Comma-separated list of *arr tag labels. When non-empty, only items
carrying at least one of these tags are searched.

- Default: empty (no include filter)
- Items with no tags fail a non-empty include filter and skip with
  `tag filter (no included tag)`.

### Tag Filter · Exclude

Comma-separated list of *arr tag labels. Items carrying any of these
tags are skipped with `tag filter (excluded tag)`.

- Default: empty (no exclude filter)
- Items with no tags always pass the exclude filter.

Labels are normalized to lowercase, stripped, and de-duplicated on
save. Labels are resolved against each instance's `/tag` endpoint at
the start of every cycle, so deleting and re-creating a tag under the
same label keeps the filter working. Renaming a tag in the *arr does
not: the old label stops matching until you update the field here.
Operator-typed labels that do not resolve to any *arr tag are logged
once per cycle as `tag filter (unknown label)` and otherwise ignored,
so an excluded tag that was renamed or mistyped stops excluding
anything until the field is fixed.

If the *arr answers the `/tag` request with an error, each enabled pass
with labels to apply (from these two fields or its own
[per-pass exclude](#per-pass-exclude)) is skipped for that cycle, and
one `tag filter (fetch failed)` info row names the skipped passes.
Passes without labels run as usual, and the cycle is then logged as an
error. An *arr that can't be reached at all is handled like any other
connection failure.

Item-to-tag mapping by app:

| App | Tags read from |
|-----|----------------|
| Radarr, Whisparr v3 | The movie row itself |
| Sonarr, Whisparr v2 | The series row that owns the episode |
| Lidarr | The artist row that owns the album |
| Readarr | The author row that owns the book |

For the skip-row format see
[Skip Reasons](/docs/reference/skip-reasons#tag-filter).

### Per-pass exclude

`Missing Tag Exclude` (under `Tag Filter · Exclude`), `Cutoff Tag
Exclude` (Cutoff Upgrades), and `Upgrade Tag Exclude` (Library
Upgrades) skip items carrying any of their tags on that one pass. They
add to `Tag Filter · Exclude` instead of replacing it, so an item
excluded there stays excluded on every pass.

- Default: empty (only `Tag Filter · Exclude` applies)
- Skips log `tag filter (excluded tag)` under the pass that made them.
- Labels on a disabled pass are not looked up or reported.

To stop Houndarr replacing a series' files while it still fills the
gaps, tag the series in Sonarr and add that tag to both `Cutoff Tag
Exclude` and `Upgrade Tag Exclude`.

These fields hold back Houndarr's searches only. The *arr's RSS sync
still grabs upgrades, and so can a missing-pass search in
Season-context, Artist-context, or Author-context mode: its
`SeasonSearch`, `ArtistSearch`, or `AuthorSearch` covers every
monitored episode, album, or book in that season, artist, or author,
including ones that already have files, and the *arr takes any upgrade
its quality profile allows. Leave the missing search mode on its
default when tagged files must stay as they are.

## Search Order

See [Search Order](/docs/concepts/search-order) for the full
explanation.

- Default: `Random` (fresh installs only; existing instances keep
  their pre-upgrade setting, typically `Chronological`)
- Alternative: `Chronological`

## Cutoff upgrade controls

### Cutoff search

Enable searching for items that do not meet your quality cutoff.

- Default: Off

### Cutoff Batch

Maximum cutoff items considered per cutoff cycle.

- Default: `1`
- Range: `1` to `250`

### Cutoff Cooldown

Minimum days before retrying the same cutoff item.

- Default: `21`
- Range: `0` to `3650`

### Cutoff Cap

Maximum successful cutoff searches per hour.

- Default: `1`
- Range: `0` to `10000`
- Set `0` to disable the cutoff hourly cap.

Cutoff searches use separate cap and cooldown from missing
searches, so the two passes do not draw from the same budget. The
missing-pass release-aware retry does not apply to cutoff searches.

### Cutoff Tag Exclude

Skips tagged items on the cutoff pass only. See
[Per-pass exclude](#per-pass-exclude).

## Library upgrade controls

### Upgrade search

Re-search items that already have files and meet your quality
cutoff. Lets your \*arr instance find better releases based on
quality profiles and custom format scoring.

- Default: Off
- Unlike cutoff search (which targets items _below_ cutoff),
  upgrade search targets items that _already meet_ cutoff.

### Upgrade Batch

- Default: `1`
- Range: `1` to `250`
- Hard cap: `5`. Engine-enforced; configured values above 5 are
  clamped to 5.

### Upgrade Cooldown (days)

- Default: `90`
- Range: `7` to `3650`
- Minimum: `7`. Engine-enforced; configured values below 7 are
  clamped to 7.

### Upgrade Cap

- Default: `1`
- Range: `0` to `10000`
- Hard cap: `5`. Engine-enforced.
- Set `0` to disable the upgrade hourly cap.

### Upgrade Tag Exclude

Skips tagged items on the upgrade pass only. See
[Per-pass exclude](#per-pass-exclude).

### Upgrade Search Mode

Per-app strategy for upgrade-pass commands. Independent of the
missing search mode.

- Sonarr, Whisparr v2: Episode (default) or Season-context
- Lidarr: Album (default) or Artist-context
- Readarr: Book (default) or Author-context
- Radarr, Whisparr v3: Always movie-level (no mode selection)

### Upgrade Series Window (Sonarr / Whisparr v2 only)

How many monitored series the upgrade pass samples per cycle. The
series offset rotates by this amount each cycle, so the whole
library cycles through over time.

- Default: `5`
- Range: `1` to `100`
- Larger windows finish a full library rotation faster but make
  every upgrade cycle hit more series at once. Raise this on a
  large Sonarr library if you can absorb the higher per-cycle load
  on the \*arr instance.
- No effect on Radarr, Whisparr v3, Lidarr, or Readarr.

### Offset-based rotation

Applies when `Search Order` is `Chronological`. Persistent per-pass
offsets (`missing_page_offset`, `cutoff_page_offset`, and upgrade
offsets) advance through the wanted list across cycles so items
further down do not starve. Offsets reset to page 1 when you save
instance settings. Upgrade offsets reset to zero when upgrade
search is toggled off.

Under `Search Order: Random`, the offset columns are still written
but not used for rotation; the engine picks a fresh random page
each cycle.

## Queue backpressure

- Default: `0` (disabled)
- Range: `0` to `10000`
- When set above zero, the cycle is skipped with reason
  `queue backpressure (N/M)` if the download queue count is at or
  above the limit.
- Fail-open: unreachable queue endpoint lets the cycle proceed.
- See
  [Skip Reasons: queue backpressure](/docs/reference/skip-reasons#queue-backpressure).

## Allowed search window

Restricts scheduled cycles to one or more time-of-day windows.

- Default: empty (24/7)
- Format: `HH:MM-HH:MM` per window, comma-separated for multiple.
  Examples: `09:00-23:00`, `09:00-12:00,18:00-22:00`,
  `22:00-06:00` (wrap-around).
- Timezone: windows interpreted in the container's local time (`TZ`
  env var; falls back to UTC).
- Boundary: start inclusive, end exclusive. `09:00-12:00` allows
  09:00:00 but blocks 12:00:00.
- DST caveat: avoid windows overlapping the spring-forward gap
  (02:00-03:00 does not exist that day) or the fall-back repeat
  (01:00-02:00 occurs twice).
- Manual `Run Now` always runs, even outside the window. The gate
  applies to scheduled cycles only.
- When the gate fires, the cycle logs one `outside allowed time
window` info row and sleeps normally. See
  [Skip Reasons: outside allowed time window](/docs/reference/skip-reasons#outside-allowed-time-window).

## Recommended starting profile

| Setting                  | Value                                      |
| ------------------------ | ------------------------------------------ |
| Missing search           | On                                         |
| Batch Size               | `2`                                        |
| Sleep (minutes)          | `30`                                       |
| Hourly Cap               | `4`                                        |
| Cooldown (days)          | `14`                                       |
| Post-Release Grace (hrs) | `6`                                        |
| Hot Retry Window (hrs)   | `0` (disabled)                             |
| Hot Retry Interval (hrs) | `2`                                        |
| Queue Limit              | `0` (disabled)                             |
| Allowed Search Window    | (blank, 24/7)                              |
| Search Order             | `Random`                                   |
| Cutoff search            | Off                                        |
| Cutoff Batch             | `1`                                        |
| Cutoff Cooldown          | `21`                                       |
| Cutoff Cap               | `1`                                        |
| Upgrade search           | Off                                        |
| Upgrade Batch            | `1` (hard cap: 5)                          |
| Upgrade Cooldown         | `90` (min: 7)                              |
| Upgrade Series Window    | `5` (range 1-100, Sonarr/Whisparr v2 only) |
| Upgrade Cap              | `1` (hard cap: 5)                          |

## Status control

Instance enabled / disabled state is controlled from the row toggle
in Settings. New instances are created as enabled by default.

<Image
img={require('@site/static/img/screenshots/houndarr-settings-instances.png')}
alt="The Houndarr Settings page showing the instance list with per-row enable toggles"
/>

<figure className="docs-screenshot-portrait">
  <Image
    img={require('@site/static/img/screenshots/houndarr-settings-mobile.png')}
    alt="The Houndarr Settings page rendered on a mobile viewport, with each instance shown as a stacked row carrying Disable, Edit, and Delete buttons"
  />
  <figcaption>
    Same list on a phone-width viewport; each row keeps its own
    Disable / Edit / Delete controls.
  </figcaption>
</figure>

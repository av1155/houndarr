---
sidebar_position: 3
title: FAQ
description: Answers to frequently asked questions and common misconceptions about Houndarr.
---

# FAQ

## "I have 500 monitored movies. Why did Houndarr only search 3?"

Because **monitored** does not mean **wanted**.

Houndarr only searches items that Sonarr or Radarr report as missing or cutoff-unmet. Of your 500 monitored movies, the vast majority are likely already downloaded and meeting your quality cutoff — so they do not appear in any wanted list, and Houndarr will not touch them.

Of the items that do appear in a wanted list, most will be filtered further by cooldowns, unreleased delays, and hourly caps. The result is often just 1–3 searches per cycle.

This is expected. See [How Houndarr Works](/docs/concepts/how-houndarr-works#the-search-funnel--why-your-search-count-is-small) for a detailed walkthrough.

## "Why is Houndarr skipping so much?"

Skips are normal. Each skip has a reason logged alongside it:

- **Cooldown** — the item was searched recently; Houndarr will retry after the cooldown window expires.
- **Unreleased delay** — the title has not been available long enough. Houndarr will retry once the delay clears.
- **Hourly cap reached** — the per-hour search limit for this instance has been hit.

A high skip count with zero errors is a **healthy sign**. It means Houndarr is examining candidates, applying its safety rules, and waiting patiently. It is not stuck.

## "Does Houndarr decide whether my file meets cutoff?"

No. Houndarr never evaluates file quality itself.

Sonarr and Radarr maintain a "Wanted → Cutoff Unmet" list based on your quality profiles. An item appears there when the file you have does not meet the profile's cutoff quality. Houndarr reads that list and schedules search commands for items on it.

If you think something should be in the cutoff list but is not, check the quality profile and the file's current quality in Sonarr/Radarr directly.

## "Why are future or recently-released titles being skipped?"

Houndarr has an **unreleased delay** setting (default: 36 hours). Items whose release date is in the future, or within the delay window of having been released, are skipped until the window clears.

This prevents Houndarr from immediately hammering indexers for content that may not be available yet. Once the delay window passes, the item becomes eligible on the next cycle.

For Radarr, Houndarr evaluates release timing using a priority chain: `digitalRelease` → `physicalRelease` → `releaseDate` → `inCinemas`. If none of those dates are set, or the title is marked as unavailable, it will be skipped.

## "Does Houndarr search my whole library?"

No. Houndarr **only** acts on what Sonarr/Radarr report as wanted. It queries two endpoints per instance:

- `wanted/missing` — items that are monitored and not yet downloaded
- `wanted/cutoff` — items that are monitored but downloaded at a quality below the cutoff

Everything else in your library is invisible to Houndarr.

## "Is a lot of skipped activity a bug?"

No. Skips are logged because transparency matters — you can see exactly why each item was not searched. The ratio of skips to searches depends on:

- How large your wanted lists are
- How recently items were searched (cooldowns)
- Whether items are inside the unreleased delay window
- How conservative your batch size and hourly cap are

If you have zero errors and occasional `searched` entries, Houndarr is operating exactly as designed.

## "Why is Houndarr searching so slowly?"

The default settings are deliberately conservative to protect your indexers:

- **Batch size 2** + **hourly cap 4** means at most 4–8 searches per hour under default settings.
- **Cooldown 14 days** means the same item is not searched twice within a two-week period.

If you want to work through your backlog faster, increase the batch size or hourly cap one step at a time and watch your indexer health for a day before increasing further. See [Increasing throughput](/docs/configuration/instance-settings#increasing-throughput).

## "Sonarr/Radarr show many cutoff-unmet items. Why is Houndarr only searching a few per day?"

This is by design. The cutoff controls (batch 1, cap 1, cooldown 21 days) are even more conservative than the missing controls, because quality upgrades are lower priority than acquiring missing content.

With a cutoff cap of 1, Houndarr will search at most 1 cutoff item per hour per instance. That is roughly 24 cutoff searches per day — enough to make steady progress without flooding indexers with upgrade requests.

If you have many cutoff-unmet items and want to work through them faster, increase the **Cutoff Cap** and **Cutoff Batch** settings gradually.

## "I enabled Houndarr but I don't see any activity"

Check these things in order:

1. Is the instance enabled (green toggle in Settings)?
2. Has at least one full sleep interval passed? (default: 30 minutes)
3. Does Sonarr/Radarr actually have anything in their wanted lists?
4. Are there any `error` entries in the Logs page?

If the wanted lists are empty, Houndarr has nothing to do. If there are errors, the error message will point to the cause.

See [Troubleshooting](/docs/concepts/troubleshooting) for a step-by-step verification guide.

## "Can Houndarr search for things that aren't in Sonarr/Radarr yet?"

No. Houndarr only triggers searches within Sonarr/Radarr for items that are already tracked in those applications. It has no ability to add new titles, browse indexers, or handle requests. For request workflows, use Overseerr or Jellyseerr alongside your *arr stack.

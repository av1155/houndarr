---
sidebar_position: 3
title: FAQ
description: Answers to frequently asked questions about Houndarr.
---

# FAQ

## "I have 500 monitored movies. Why did Houndarr only search 3?"

Monitored doesn't mean wanted. Most of your library is already downloaded and meeting your quality cutoff, so those items never appear in a wanted list. Of the items that are wanted, cooldowns, post-release grace windows, and hourly caps filter out most of the rest.

See [How Houndarr Works](/docs/concepts/how-houndarr-works#why-only-a-few-items-get-searched-each-cycle) for the full breakdown.

## "Why is Houndarr skipping so much?"

Each skip has a reason logged alongside it — cooldown, post-release grace, hourly cap, or queue backpressure. A high skip count with zero errors means Houndarr is pacing itself correctly. See [How Houndarr Works](/docs/concepts/how-houndarr-works#what-skipped-means-in-the-logs) for what each reason means.

## "Does Houndarr decide whether my file meets cutoff?"

No. Your *arr instance maintains the "Wanted > Cutoff Unmet" list based on your quality profiles. Houndarr reads that list and schedules searches for items on it. If something should be in the cutoff list but isn't, check the quality profile in your instance directly.

## "Why are future or recently-released titles being skipped?"

Items with no release date or a release date in the future are always skipped (`not yet released`). Items that have been released but are still within the **post-release grace** window (default: 6 hours) are also skipped until the window clears. This avoids hammering indexers for content that may not be available yet.

For Radarr, release timing uses a priority chain: `digitalRelease` → `physicalRelease` → `releaseDate` → `inCinemas`. If none of those dates are set, or the title is marked as unavailable, it will be skipped.

## "What does 'queue backpressure' mean in the logs?"

If you set a **Queue Limit** on an instance, Houndarr checks the download queue before each cycle. When the queue count meets or exceeds the limit, the entire cycle is skipped and logged as `queue backpressure (N/M)`. This prevents Houndarr from adding searches when the download client is already busy. If the queue endpoint is unreachable, the search proceeds normally.

## "Does Houndarr search my whole library?"

No. It only acts on what your *arr instances report as wanted — items from `wanted/missing` and `wanted/cutoff`. Everything else in your library is invisible to Houndarr.

## "Why is Houndarr searching so slowly?"

The defaults are conservative on purpose — batch size 2, hourly cap 4, 14-day cooldown. With a large backlog, clearing it takes weeks. You can increase throughput gradually; see [Increasing throughput](/docs/configuration/instance-settings#increasing-throughput).

## "My *arr instance shows many cutoff-unmet items. Why is Houndarr only searching a few per day?"

The cutoff controls (batch 1, cap 1, cooldown 21 days) are more conservative than missing controls because quality upgrades are lower priority than acquiring missing content. With a cap of 1, that's roughly 24 cutoff searches per day. Increase **Cutoff Cap** and **Cutoff Batch** gradually if you want faster progress.

## "I enabled Houndarr but I don't see any activity"

Check in order: is the instance enabled (green toggle)? Has at least one sleep interval passed (default: 30 min)? Does your *arr instance actually have items in its wanted lists? Are there `error` entries in the Logs page?

See [Troubleshooting](/docs/concepts/troubleshooting) for a step-by-step guide.

## "Can Houndarr search for things that aren't in my *arr instance yet?"

No. Houndarr only triggers searches within your *arr instances for items already tracked there. For request workflows, use Overseerr or Jellyseerr alongside your *arr stack.

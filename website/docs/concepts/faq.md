---
sidebar_position: 3
title: FAQ
description: Answers to frequently asked questions about Houndarr.
---

# FAQ

## "Why not just use Sonarr's built-in search?"

Sonarr, Radarr, and the other *arr apps monitor RSS feeds for new releases as they appear on your indexers. RSS doesn't re-search for content that was already available before you set up your current indexers or changed your quality profile.

Each app has a "Search All Missing" button, but it fires every missing item at once. With a large backlog, that floods your indexers with hundreds of simultaneous requests and can get you rate-limited or banned. Per-item manual search works but is tedious when you have hundreds of entries to work through.

Houndarr fills the gap by working through your wanted lists automatically in small, rate-limited batches over time. Common scenarios where it helps: adding a new indexer, changing quality profiles, recovering after downtime, or setting up a new server with a large existing library. If your current setup already grabs everything and handles upgrades automatically, you don't need Houndarr.

## "I have 500 monitored movies. Why did Houndarr only search 3?"

Monitored doesn't mean wanted. Most of your library is already downloaded and meeting your quality cutoff, so those items never appear in a wanted list. Of the items that are wanted, cooldowns, post-release grace windows, and hourly caps filter out most of the rest.

See [How Houndarr Works](/docs/concepts/how-houndarr-works#why-only-a-few-items-get-searched-each-cycle) for the full breakdown.

## "Why is Houndarr skipping so much?"

Each skip has a reason logged alongside it: `on cooldown (Nd)`, `post-release grace (Nh)`, hourly cap, or queue backpressure. A high skip count with zero errors means Houndarr is pacing itself correctly. See [How Houndarr Works](/docs/concepts/how-houndarr-works#what-skipped-means-in-the-logs) for what each reason means.

## "Does Houndarr decide whether my file meets cutoff?"

No. Your *arr instance maintains the "Wanted > Cutoff Unmet" list based on your quality profiles. Houndarr reads that list and schedules searches for items on it. If something should be in the cutoff list but isn't, check the quality profile in your instance directly.

## "Why are future or recently-released titles being skipped?"

Items with no release date or a release date in the future are always skipped (`not yet released`). Items that have been released but are still within the **post-release grace** window (default: 6 hours) are also skipped until the window clears. This avoids hammering indexers for content that may not be available yet.

When that release-timing block clears, the missing pass can retry the item once right away instead of waiting for the full missing cooldown. Cutoff searches do not use that early retry.

For Radarr, release timing uses a priority chain: `digitalRelease` → `physicalRelease` → `releaseDate` → `inCinemas`. If none of those dates are set, or the title is marked as unavailable, it will be skipped.

## "What does 'queue backpressure' mean in the logs?"

If you set a **Queue Limit** on an instance, Houndarr checks the download queue before each cycle. When the queue count meets or exceeds the limit, the entire cycle is skipped and logged as `queue backpressure (N/M)`. This prevents Houndarr from adding searches when the download client is already busy. If the queue endpoint is unreachable, the search proceeds normally.

## "Does Houndarr search my whole library?"

It searches items from `wanted/missing` and `wanted/cutoff`, rotating through the list over time so every item gets evaluated even if the first pages are on cooldown. If upgrade search is enabled, it also re-searches library items that already meet cutoff, rotating through your library with a separate offset. Everything else is untouched.

The default `Search Order` is `Random`, which picks a random page each cycle and shuffles the items on it. Switch to `Chronological` if you prefer deterministic oldest-first rotation. See [Search Order](/docs/configuration/instance-settings#search-order) for the trade-off.

## "Why is Houndarr searching so slowly?"

The defaults are conservative on purpose (batch size 2, hourly cap 4, 14-day cooldown). With a large backlog, clearing it takes weeks. You can increase throughput gradually; see [Increasing throughput](/docs/configuration/instance-settings#increasing-throughput).

## "My *arr instance shows many cutoff-unmet items. Why is Houndarr only searching a few per day?"

The cutoff controls (batch 1, cap 1, cooldown 21 days) are more conservative than missing controls because quality upgrades are lower priority than acquiring missing content. With a cap of 1, that's roughly 24 cutoff searches per day. Increase **Cutoff Cap** and **Cutoff Batch** gradually if you want faster progress.

## "I enabled Houndarr but I don't see any activity"

Check in order: is the instance enabled (green toggle)? Has at least one sleep interval passed (default: 30 min)? Does your *arr instance actually have items in its wanted lists? Are there `error` entries in the Logs page?

See [Troubleshooting](/docs/concepts/troubleshooting) for a step-by-step guide.

## "What is upgrade search? How is it different from cutoff?"

Cutoff search targets items your *arr instance flags as *below* your quality cutoff (they don't meet your minimum standard). Upgrade search targets items that *already meet* cutoff but might have better releases available based on quality profiles and custom format scoring. Upgrade search reads the full library rather than the `wanted/cutoff` list, and uses much more conservative defaults (batch 1, cooldown 90 days, hourly cap 1, hard caps enforced). Enable it only after missing and cutoff backlogs are stable. When upgrade search triggers a search, your *arr instance evaluates the results against your quality profile and custom format scores; Houndarr does not influence that decision.

## "Can Houndarr search for things that aren't in my *arr instance yet?"

No. Houndarr only triggers searches within your *arr instances for items already tracked there. For request workflows, use Overseerr or Jellyseerr alongside your *arr stack.

## "I deleted files to free up space. Will Houndarr re-download them?"

If the items are still monitored in your *arr instance, yes: they will appear in the wanted/missing list and Houndarr will eventually search for them. To prevent re-downloads, unmonitor the items in your *arr instance before or after deleting the files. Houndarr only acts on what your *arr instance reports as wanted.

## "Why are the same few series or movies showing up over and over in the logs?"

That pattern used to be common with chronological search order: same-day releases cluster together in the *arr wanted list (it falls back to title order within equal dates), so the logs looked like long alphabetical runs of the same show or studio. The default is now `Random`, which picks a random page of your wanted list each cycle and shuffles items on it before searching. If you still prefer the old deterministic behaviour, switch `Search Order` to `Chronological` in the Edit Instance form.

## "Does Houndarr respect custom format scores?"

Houndarr does not evaluate quality, custom formats, or release attributes. It only triggers search commands. Your *arr instance handles all quality evaluation, custom format scoring, and download decisions. If your quality profile and custom formats are configured correctly, Houndarr's searches will automatically produce results that satisfy them. If you want to build, test, and deploy quality profiles and custom formats across your stack, [Profilarr](https://github.com/Dictionarry-Hub/profilarr) is a community tool built for that.

---
sidebar_position: 10
title: FAQ
description: Short answers to the questions that come up most often about Houndarr.
---

# FAQ

Short answers to the most common questions. For details, each answer
links to the full coverage.

## Why not just use Sonarr's built-in search?

Sonarr, Radarr, and the other *arr apps monitor RSS feeds for new
releases as they appear on your indexers. RSS does not re-search
content that was already available before you set up your current
indexers or changed your quality profile.

Each app has a "Search All Missing" button, but it fires every
missing item at once. With a large backlog, that floods your
indexers with hundreds of simultaneous requests and can get you
rate-limited or banned. Per-item manual search works but is tedious
at the scale of hundreds of entries.

Houndarr fills the gap by working through your wanted lists
automatically in small, rate-limited batches. Common situations
where it helps: adding a new indexer, changing quality profiles,
recovering after downtime, or setting up a new server with a large
existing library. If your current setup already grabs everything
and handles upgrades automatically, you do not need Houndarr.

## I have 500 monitored movies. Why did Houndarr only search 3?

Monitored does not mean wanted. Most of your library is already
downloaded and meeting your quality cutoff, so those items never
appear in a wanted list. Of the items that are wanted, cooldowns,
post-release grace windows, and hourly caps filter out most of the
rest.

See
[How Houndarr Works](/docs/concepts/how-scheduling-works#why-only-a-few-items-get-searched-each-cycle)
for the full funnel.

## Why is Houndarr skipping so much?

Each skipped row logs a reason string that tells you why. A high
skip count with zero errors is the engine pacing itself. Errors are
the signal that something is wrong; skips are not. See
[Skip Reasons](/docs/reference/skip-reasons) for what each reason
string means.

## Does Houndarr decide whether my file meets cutoff?

No. Your *arr instance maintains the Wanted -> Cutoff Unmet list
based on your quality profiles. Houndarr reads that list and
schedules searches for items on it. If something should be on the
cutoff list but is not, check the quality profile in your instance
directly.

## Does Houndarr search my whole library?

It searches items from `wanted/missing` and `wanted/cutoff`,
rotating through the list over time so every item gets evaluated
even when the first pages are on cooldown. With upgrade search
enabled, it also re-searches library items that already meet
cutoff, rotating through your library with a separate offset.
Everything else is untouched. See
[Search Order](/docs/concepts/search-order) for the rotation
mechanics.

## Why is Houndarr searching so slowly?

The defaults ship with batch size 2, hourly cap 4, and a 14-day
cooldown. With a large backlog, clearing it takes weeks. You can
raise throughput gradually; see
[Increase Throughput](/docs/guides/increase-throughput).

## What is upgrade search? How is it different from cutoff?

Cutoff search targets items your *arr instance flags as *below*
your quality cutoff (they do not meet your minimum standard).
Upgrade search targets items that *already meet* cutoff but might
have better releases available based on quality profiles and custom
format scoring.

Upgrade search reads the full library rather than the
`wanted/cutoff` list, and the engine enforces hard caps: batch
capped at 5, cooldown floored at 7 days (default 90), hourly cap
capped at 5. Enable it only after missing and cutoff backlogs are
stable. When an upgrade search triggers, your *arr instance
evaluates the results against your quality profile and custom
format scores; Houndarr does not influence that decision.

## Can Houndarr search for things that aren't in my *arr instance yet?

No. Houndarr only triggers searches within your *arr instances for
items already tracked there. For request workflows, use Overseerr
or Jellyseerr alongside your *arr stack.

## I deleted files to free up space. Will Houndarr re-download them?

If the items are still monitored in your *arr instance, yes: they
will appear in the `wanted/missing` list and Houndarr will
eventually search for them. To prevent re-downloads, unmonitor the
items in your *arr instance before or after deleting the files.
Houndarr only acts on what your *arr instance reports as wanted.

## Does Houndarr respect custom format scores?

Houndarr does not evaluate quality, custom formats, or release
attributes. It only triggers search commands. Your *arr instance
handles all quality evaluation, custom format scoring, and
download decisions. When your quality profile and custom formats
are set up, Houndarr's searches produce results that satisfy them.
If you want help building, testing, and deploying quality profiles
and custom formats across your stack,
[Profilarr](https://github.com/Dictionarry-Hub/profilarr) is a
community tool built for that.

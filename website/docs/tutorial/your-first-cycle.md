---
sidebar_position: 1
title: Your First Search Cycle
description: A guided walkthrough of adding an *arr instance, triggering a search on demand, and reading what happens in the first log cycle.
---

import Image from '@theme/IdealImage';

# Your First Search Cycle

This tutorial walks through the first 30 minutes with Houndarr: add
a Sonarr or Radarr instance, run a search on demand, and read the
log output. The goal is not to finish configuration. The goal is
to see one cycle happen and learn what the log rows mean.

## Prerequisites

Before you start, confirm:

- Houndarr is running. Follow
  [Quick Start](/docs/guides/installation/docker-compose) or
  [Install on Unraid](/docs/guides/installation/unraid) if not.
- You have at least one *arr instance reachable from the Houndarr
  host. Sonarr and Radarr are the most common; any of the six
  supported types work (Sonarr, Radarr, Lidarr, Readarr, Whisparr
  v2, Whisparr v3).
- The *arr instance has at least one monitored item on its wanted
  list. If you are brand new to *arr, the
  [Servarr Sonarr quick-start guide](https://wiki.servarr.com/sonarr/quick-start-guide)
  walks you through getting Sonarr running with one series. Budget
  15 to 30 minutes for that detour.
- An admin account is created on Houndarr. See
  [First-Run Setup](/docs/guides/first-run-setup) if the
  browser still shows the setup screen.

## Step 1: add your *arr instance

In the Houndarr top navigation, click **Settings**.

<Image
  img={require('@site/static/img/screenshots/houndarr-settings-instances.png')}
  alt="The Houndarr Settings page showing an Instances table with an Add Instance button in the top right"
/>

Click **+ Add Instance** in the top right. Fill in the form:

- **Name**: anything descriptive. "Radarr Movies" or "Sonarr 4K"
  are fine.
- **Type**: pick the *arr app type. Radarr for movies, Sonarr for
  TV episodes, and so on.
- **URL**: the base URL of your instance. In a Docker Compose
  stack this is usually the container service name plus the
  instance's internal port: `http://sonarr:8989`,
  `http://radarr:7878`. Not `localhost`.
- **API Key**: copy from your *arr instance. In Sonarr or Radarr,
  go to **Settings > General** and find the **API Key** field.

<Image
  img={require('@site/static/img/screenshots/houndarr-add-instance-form.png')}
  alt="The Houndarr Add Instance modal with Connection fields (Name, Type, URL, API Key) and Search Policy fields (Batch Size, Sleep, Hourly Cap, Cooldown, Post-Release Grace, Queue Limit)"
/>

Leave every Search Policy field at its default for now. This is a
first cycle, not a tuning run.

Click **Save**. Houndarr does a connection check against the *arr
API. On success, the new row appears in the Instances table with a
green **Active** dot.

## Step 2: trigger the first cycle

Click **Dashboard** in the top navigation.

<Image
  img={require('@site/static/img/screenshots/houndarr-dashboard-instances.png')}
  alt="The Houndarr Dashboard Instances section with per-instance cards showing WANTED / ELIGIBLE / SEARCHED stats, Cooldown schedule panel, policy chips, and Run Now button"
/>

Find the card for the instance you just added and click **Run
Now**. This bypasses the `Sleep (minutes)` schedule and starts a
cycle immediately. The button flips through Running then Queued for
a few seconds while the engine probes the wanted list, picks
candidates, and decides who to search.

## Step 3: read the log

Click **Logs** in the top navigation. The first-cycle rows appear
at the top of the table.

<Image
  img={require('@site/static/img/screenshots/houndarr-logs.png')}
  alt="The Houndarr Logs page showing filter controls, cycle summary stats, and a table of skipped and searched rows from Sonarr and Radarr cycles"
/>

Look for the cycle group header for your instance. It shows an
**outcome** label:

- **outcome searched**: at least one item was searched this cycle.
- **outcome skips only**: the cycle evaluated candidates but none
  were eligible right now. This is common on a first run against a
  wanted list where everything was imported around the same time.

Each individual row inside the cycle shows one evaluated item:

- An `action=searched` row means a search command was sent to your
  *arr instance. Check your *arr's Activity or History tab; a
  matching grab attempt should be visible around the same
  timestamp.
- An `action=skipped` row has a reason string like
  `on cooldown (Nd)`, `not yet released`, or
  `post-release grace (Nh)`. None of those mean anything is
  broken. See
  [Skip Reasons](/docs/reference/skip-reasons) for what each
  reason means and why skipping is normal.

If you see any `action=error` rows, the connection is the usual
culprit. See
[Troubleshoot Connection](/docs/guides/troubleshoot-connection)
for common fixes: wrong API key, unreachable URL, published vs
internal port confusion.

## What you have now

- An instance wired to Houndarr, running on the default schedule
  (batch 2, cap 4 per hour, cooldown 14 days).
- One log cycle on file showing what a typical pass looks like.
- A feel for the difference between `searched` and `skipped` rows.

## What to do next

The first cycle is the boring part. The more useful parts:

1. Let Houndarr run for 24 hours. Come back to the Logs page. You
   should see 4 to 8 searched rows across the day per instance,
   plus many skips. That is pacing working as designed.
2. Add any other *arr instances you want on the schedule. Each one
   gets its own budget.
3. When you hit a plateau (cooldowns filling up, nothing new to
   search), tune throughput deliberately. The order of adjustments
   is in [Increase Throughput](/docs/guides/increase-throughput).
4. Read [How Houndarr Works](/docs/concepts/how-scheduling-works)
   for the mental model of the whole search cycle.

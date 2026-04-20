---
sidebar_position: 4
title: First-Run Setup
description: Create the admin account, add your first *arr instance, and confirm the connection test passes after a fresh Houndarr install.
---

import Image from '@theme/IdealImage';

# First-Run Setup

After starting Houndarr for the first time, follow these steps to get it configured.

## 1. Create an admin account

Navigate to `http://<your-host>:8877`. You will see the setup screen prompting you
to create an admin username and password.

- Choose a strong password (Houndarr enforces minimum complexity requirements).
- This is the only account. Houndarr uses a single-admin authentication model.

## 2. Log in

After creating your account, log in with your new credentials. The
Dashboard greets you with an empty-state panel until you add your
first instance:

<Image
  img={require('@site/static/img/screenshots/houndarr-dashboard-empty.png')}
  alt="The Houndarr Dashboard in its empty first-run state: the subheader reads 'No hounds on patrol yet.' above a centered panel with a dashed-circle icon, 'No instances configured' title, body copy naming Sonarr, Radarr, Lidarr, Readarr, and Whisparr, and a primary '+ Add your first instance' button"
/>

Click **Add your first instance** or open the **Settings** link in
the top nav to continue.

## 3. Add your instances

Go to **Settings** and click **Add Instance** to connect your *arr instances.

<Image
  img={require('@site/static/img/screenshots/houndarr-settings-instances.png')}
  alt="The Houndarr Settings page showing a two-row Instances table with active Radarr and Sonarr rows, each with Disable / Edit / Delete actions"
/>

For each instance you need:

- **Name**: a friendly label (e.g., "Radarr Movies", "Sonarr 4K", "Lidarr Music")
- **Type**: Radarr, Sonarr, Lidarr, Readarr, Whisparr v2, or Whisparr v3
- **URL**: the base URL of the instance (e.g., `http://sonarr:8989`). For Docker Compose, this must be the *arr's internal container port, not the host port you published. See [Troubleshoot Connection](/docs/guides/troubleshoot-connection) if the connection test fails.
- **API Key**: found in your *arr instance under Settings > General

:::tip
API keys are encrypted at rest using Fernet symmetric encryption and are never
sent back to the browser. See [Credential Handling](/docs/security/credential-handling)
for details.
:::

## 4. Configure search settings

Each instance has its own search settings. The defaults are tuned
to stay well under typical indexer limits:

| Setting | Default | Purpose |
|---------|---------|---------|
| Batch Size | 2 | Items per search cycle |
| Sleep (minutes) | 30 | Wait between cycles |
| Hourly Cap | 4 | Max searches per hour |
| Cooldown (days) | 14 | Min days before re-searching an item |
| Post-Release Grace (hrs) | 6 | Hours to wait after release date before searching |
| Queue Limit | 0 (disabled) | Skip cycle when download queue meets or exceeds this count |

For detailed explanations of all settings, see [Instance Settings](/docs/reference/instance-settings).

<Image
  img={require('@site/static/img/screenshots/houndarr-add-instance-form.png')}
  alt="The Houndarr Add Instance modal with Connection fields (Name, Type, URL, API Key) and Search Policy fields (Batch Size, Sleep, Hourly Cap, Cooldown, Post-Release Grace, Queue Limit)"
/>

## 5. Enable the instance

Toggle the instance to **Enabled** in the Settings page. Houndarr will begin
searching on the configured schedule.

## The Dashboard

Once instances are enabled, the Dashboard has two main sections.

### Library health

The top section carries an **adaptive subheader** with an *N of M
hounds on patrol* sentence and the most recent dispatch timestamp, a
**library-health bar** with five gating segments (eligible, cutoff
cooldown, cooldown, upgrade cooldown, unreleased) summed across every
configured instance, and a **Recent hunts** strip listing the last 5
dispatches in the past 7 days, each in its instance's type color.

<Image
  img={require('@site/static/img/screenshots/houndarr-dashboard-library-health.png')}
  alt="The Houndarr Dashboard library-health section with the adaptive subheader, a five-segment library-health bar, and the Recent hunts strip of the last five dispatches"
/>

On mobile the subheader and library-health bar stack vertically and
the Recent hunts strip becomes a scrollable list.

<figure className="docs-screenshot-portrait">
  <Image
    img={require('@site/static/img/screenshots/houndarr-dashboard-library-health-mobile.png')}
    alt="The Houndarr Dashboard library-health section rendered on a phone-width viewport with the subheader, gating bar, legend, and Recent hunts list stacked vertically"
  />
  <figcaption>
    Library-health section on a phone-width viewport.
  </figcaption>
</figure>

### Instances

The lower section lays out one **card per instance** with a type
eyebrow, instance name, 3-stat row (`WATCHING` monitored total,
`ELIGIBLE` ready-to-search count, and `SEARCHED` lifetime dispatches),
a **Cooldown schedule** inset panel showing the soonest, median, and
latest items to unlock with their titles and time-until-unlock, a
policy chip row with tooltips, and a type-colored **Run Now** outline
button. Cards also carry an **error banner** and a red `N errors`
pill whenever the latest `search_log` row is an error (both deep-link
to the Logs page filtered to that instance), and a **disabled-card
treatment** (dim border, muted stats, `paused` footer, disabled Run
Now) when `enabled=0`.

<Image
  img={require('@site/static/img/screenshots/houndarr-dashboard-instances.png')}
  alt="The Houndarr Dashboard Instances section showing per-instance cards with WATCHING / ELIGIBLE / SEARCHED stats, Cooldown schedule panel, policy chips, and Run Now button"
/>

On mobile the grid collapses into a single column and the section
header keeps the same rule + right-aligned **+ Add Instance** shortcut
you see on desktop.

<figure className="docs-screenshot-portrait">
  <Image
    img={require('@site/static/img/screenshots/houndarr-dashboard-instances-mobile.png')}
    alt="The Houndarr Dashboard Instances section on a phone-width viewport with cards stacked one per row and the + Add Instance link aligned to the right of the section header"
  />
  <figcaption>
    Instances section on a phone-width viewport.
  </figcaption>
</figure>

---
sidebar_position: 2
title: Troubleshooting
description: How to verify Houndarr is working correctly, interpret logs, and diagnose common issues.
---

# Troubleshooting

## How to verify Houndarr is working

If you are unsure whether Houndarr is actually doing anything, follow these steps before assuming something is broken.

### Step 1 — Open Sonarr/Radarr's own wanted pages

In Sonarr: **Wanted → Missing** and **Wanted → Cutoff Unmet**  
In Radarr: **Movies → Discover** or use the **Wanted** filter

If those pages are empty, Houndarr has nothing to search. A Houndarr with an empty wanted list will log `skipped` for every item it considers, and that is completely correct behavior.

### Step 2 — Compare items to Houndarr logs

Open Houndarr's **Logs** page and look at recent activity. Each row shows:

| Field | What it tells you |
|-------|-------------------|
| **Action** | `searched` — a search command was sent; `skipped` — item was ineligible this cycle; `error` — something went wrong |
| **Reason** | Why the item was skipped or what kind of search was triggered |
| **Item label** | The series/movie title and episode info |
| **Timestamp** | When the action occurred |

If you see `searched` entries for items that also appear in Sonarr/Radarr's wanted views, Houndarr is working correctly.

### Step 3 — Check "Last Searched" timestamps in Sonarr/Radarr

In Sonarr's cutoff-unmet view, each episode shows when it was last searched. If those timestamps match recent Houndarr log entries, you have confirmed end-to-end that the search commands are reaching Sonarr and being executed.

### Step 4 — Expect skips for cooldown and unreleased items

If most of your log entries say `skipped`, check the reasons:

- **`cooldown (N days remaining)`** — the item was searched recently; Houndarr is waiting before trying again. This is intentional.
- **`unreleased delay (N hrs remaining)`** — the release date is too recent or in the future. Houndarr will search once the delay window clears.
- **`hourly cap reached`** — your per-hour search limit has been hit for this cycle.

None of these are errors. They are all normal scheduling behavior.

### Step 5 — Zero errors is a strong health signal

Look at your logs. If you see:

- Many `skipped` entries
- Some `searched` entries
- **Zero `error` entries**

Houndarr is healthy. It is examining candidates, applying its rules, and issuing searches at the configured rate. The absence of errors means it is connecting to Sonarr/Radarr successfully and the search commands are being accepted.

### Step 6 — Understand that conservative settings progress slowly but correctly

The default settings are intentionally conservative:

- **Batch size 2** — only 2 items per cycle
- **Hourly cap 4** — maximum 4 searches per hour
- **Cooldown 14 days** — no item is searched more than once every two weeks

With these defaults, Houndarr might search 4–8 items in a day. If your backlog has hundreds of items, clearing it will take weeks. That is by design. You can increase throughput by raising the batch size or hourly cap, but do so gradually and monitor your indexer health.

See [Instance Settings](/docs/configuration/instance-settings#increasing-throughput) for the recommended order of adjustments.

---

## Common issues

### Houndarr is not connecting to Sonarr/Radarr

**Symptoms:** `error` entries in logs with connection refused or timeout messages.

**Checks:**
1. Verify the instance URL is reachable from the Houndarr container (try `curl <url>/api/v3/system/status?apikey=<key>` from inside the container).
2. Confirm the API key is correct in Houndarr's Settings page.
3. If Sonarr/Radarr are in the same Docker Compose stack, use the container service name as the hostname (e.g., `http://sonarr:8989`), not `localhost`.
4. Check that the URL does not have a trailing slash.

### An instance is enabled but nothing is happening

**Checks:**
1. Open Sonarr/Radarr's wanted pages. If they are empty, there is nothing for Houndarr to search.
2. Check the Houndarr Logs page for the instance. Look at the most recent entries — if you see `skipped` with reason `hourly cap reached`, wait until the next hour window.
3. Confirm the instance is enabled (green toggle in Settings).
4. Check that the sleep interval has elapsed since the last cycle. With a 30-minute sleep, Houndarr runs approximately twice per hour.

### Cutoff search is not running

**Checks:**
1. Confirm **Cutoff search** is enabled for the instance (it is off by default).
2. Open Sonarr/Radarr's "Wanted → Cutoff Unmet" view. If it is empty, there is nothing to search.
3. Check your quality profiles in Sonarr/Radarr. An item only appears in the cutoff-unmet list if the file you have does not meet the profile's cutoff quality.
4. Note that cutoff search uses a separate hourly cap (default: 1 per hour). With a cap of 1, you may only see one cutoff search per hour.

### I see errors in the logs

`error` log entries include a message field explaining what went wrong. Common causes:

- **HTTP 401** — API key is wrong or has been rotated in Sonarr/Radarr.
- **HTTP 404** — The item was removed from Sonarr/Radarr between the time Houndarr read the wanted list and the time it issued the search.
- **Connection refused / timeout** — Sonarr/Radarr is unreachable (see connection troubleshooting above).

Occasional 404 errors are harmless. A stream of connection errors suggests a network or configuration issue.

### The Dashboard shows "Next run" but nothing happens

The "Next run" time is an estimate based on the sleep interval. If the container was recently restarted, the first cycle will run after one full sleep interval. Check the Logs page to confirm whether a cycle has completed.

---
sidebar_position: 8
title: Troubleshoot Connection
description: Fix connection errors, missing cycles, missing cutoff activity, and other common issues.
---

# Troubleshoot Connection

When something looks wrong in the logs, these are the usual causes
and fixes. Confirm Houndarr is actually running first; see
[Verify It's Working](/docs/guides/verify-its-working) if you are
not sure.

## Houndarr cannot connect to your *arr instance

Symptoms: an `error` row reading `Could not reach <url>` in the
Logs page, and the instance marked offline on the dashboard.
Houndarr logs that row once per outage and retries every 30
seconds; an `is reachable again` row follows once the *arr answers.
If the instance was already failing before the outage and Houndarr
reconnects without searching (a full download queue, a closed search
window), that row is an `error` ending in
`its last error is not rechecked yet: <error>`, so the instance keeps
showing that error until its next search.

Checks:

1. Verify the instance URL is reachable from the Houndarr
   container. Try
   `curl <url>/api/v3/system/status?apikey=<key>` from inside the
   container. Use `/api/v1/` for Lidarr and Readarr instead of
   `/api/v3/`.
2. Confirm the API key is correct in Houndarr's Settings page.
3. When both containers live in the same Docker Compose stack, use
   the container service name plus the *arr's internal listen port
   as the hostname. Examples: `http://sonarr:8989`,
   `http://radarr_hd:7878`. Not `localhost`. Container names with
   underscores in them are allowed.
4. The port must be the *arr's internal listen port, not the
   published host port from your `docker-compose.yml`. A mapping
   like `6970:6969` means Houndarr still reaches the *arr at
   `:6969`; the published `6970` only exists on your host, not on
   the Docker network the two containers share.
5. Default internal ports: Sonarr 8989, Radarr 7878, Lidarr 8686,
   Readarr 8787, Whisparr v2 and v3 both 6969.
6. When the *arr instance runs directly on the container host
   rather than in the same stack, use
   `http://host.docker.internal:<port>` (Docker) or
   `http://host.containers.internal:<port>` (Podman). Both are
   runtime-provided aliases for the host and use the published
   port on the host side.
7. Check that the URL does not have a trailing slash.

## An instance is enabled but nothing is happening

Checks:

1. Open your *arr instance's wanted pages. If they are empty,
   there is nothing for Houndarr to search.
2. Check the Houndarr Logs page for the instance. When the most
   recent entries say `skipped` with reason
   `hourly limit reached`, wait until the next hour window.
3. Confirm the instance is enabled (green toggle in Settings).
4. Check that the sleep interval has elapsed since the last
   cycle. With a 30-minute sleep, Houndarr runs roughly twice per
   hour.

## Cutoff search is not running

Checks:

1. Confirm Cutoff search is enabled for the instance. It is off
   by default.
2. Open your *arr instance's **Wanted -> Cutoff Unmet** view. If
   it is empty, there is nothing to search.
3. Check your quality profiles in your *arr instance. An item
   only appears in the cutoff-unmet list when the file you have
   does not meet the profile's cutoff quality.
4. Cutoff search uses a separate hourly cap (default 1 per hour).
   At cap 1, you may see only one cutoff search per hour.

## I see errors in the logs

`error` rows include a message field explaining what went wrong.
Common causes:

- HTTP 401 or 403: the API key is wrong, your *arr instance
  rotated the key, or a reverse proxy in front of it wants its own
  login. On an instance that only runs upgrade search this reads
  `upgrade pool fetch failed: HTTP 401 from <path>`.
- A redirect status such as HTTP 302: usually a reverse proxy
  sending Houndarr to its login page.
- HTTP 404: the item was removed from the instance between the
  time Houndarr read the wanted list and the time it issued the
  search. Occasional 404s are harmless. A repeating
  `upgrade pool fetch failed: HTTP 404` points at a wrong URL base
  instead.
- `Could not reach <url>`: the instance is unreachable. See the
  first section above.
- `API key can't be decrypted with the current master key; enter it again in Settings`:
  `houndarr.masterkey` was lost or replaced, so the instance does not
  search until its key is entered again. See
  [Backup and Restore](/docs/guides/backup-and-restore#what-happens-when-the-master-key-is-lost).

An error that repeats every cycle needs fixing; a single 404 does
not. Once a cycle runs cleanly again, Houndarr logs
`'<name>' is healthy again` and the dashboard clears the instance.

## Houndarr exits right after it starts

Check the container log for a CRITICAL line. One that names
`houndarr.masterkey` means the file exists but holds no valid key;
Houndarr exits with code 1 instead of replacing it. See
[Backup and Restore](/docs/guides/backup-and-restore#a-damaged-master-key-file).

## Dashboard shows "next patrol" but nothing happens

The "next patrol" countdown on each card is an estimate based on the
sleep interval. After a container restart, the first cycle runs after
one full sleep interval. Check the Logs page to confirm whether a
cycle actually completed.

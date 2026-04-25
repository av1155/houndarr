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

Symptoms: `error` rows in the Logs page with "connection refused"
or "timeout" in the message field.

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

- HTTP 401: the API key is wrong, or your *arr instance rotated
  the key.
- HTTP 404: the item was removed from the instance between the
  time Houndarr read the wanted list and the time it issued the
  search. Occasional 404s are harmless.
- Connection refused or timeout: the instance is unreachable.
  See the first section above.

A stream of connection errors suggests a network or configuration
problem. A single 404 or 401 usually does not.

## Dashboard shows "next patrol" but nothing happens

The "next patrol" countdown on each card is an estimate based on the
sleep interval. After a container restart, the first cycle runs after
one full sleep interval. Check the Logs page to confirm whether a
cycle actually completed.

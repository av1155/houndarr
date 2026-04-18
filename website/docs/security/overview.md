---
sidebar_position: 1
title: Security Overview
description: What Houndarr protects, what it does not, and how to harden a deployment.
---

# Security Overview

This page covers Houndarr's outbound behavior, its container
security posture, and a hardening checklist for production installs.
For credential internals (API key encryption, session handling) see
[Credential Handling](/docs/security/credential-handling). For the
formal security boundary (what is in scope, what is not) see
[Threat Model](/docs/security/threat-model). For independent audit
results see [Audit](/docs/security/audit).

If you find a discrepancy between this page and the code,
[report it](https://github.com/av1155/houndarr/security/advisories/new).

## Does Houndarr call home?

**No.** The Houndarr server makes zero outbound connections to any
developer, analytics, telemetry, or third-party service. No update
checks, no version polling, no usage reporting, no crash reporting,
no beacons.

### What the server connects to

Only your own *arr instances (Radarr, Sonarr, Lidarr, Readarr,
Whisparr), over their standard REST API (v3 for Radarr / Sonarr /
Whisparr, v1 for Lidarr / Readarr). Each request carries:

- `X-Api-Key`: the API key you configured
- `Accept: application/json`
- Standard API parameters (pagination, command IDs)

Nothing about Houndarr, your config, or your usage appears in those
requests. The HTTP client library is `httpx`; no other HTTP library
is present in the source tree.

### What the server does not connect to

- No analytics, error tracking, or telemetry services
- No developer-controlled servers
- No package registries, update servers, or version-check endpoints

### Browser-side CDN resources

The web UI loads two JavaScript libraries from external CDNs:

- Tailwind CSS from `cdn.tailwindcss.com` (Play CDN)
- HTMX 2.0.4 from `unpkg.com` (pinned version)

Your browser fetches these, not the Houndarr server. CDN providers
log the standard metadata any CDN sees (IP, User-Agent). The
Houndarr server itself never contacts these CDNs.

A footer link to the Houndarr GitHub repository is present in the UI
but only loads when clicked.

## Container security posture

### Non-root execution

The Docker container starts as root only to perform PUID/PGID
remapping. After remapping, the entrypoint uses `gosu` to drop to
the non-root `appuser` before exec-ing the application. The
Houndarr process itself never runs as root.

### No added capabilities

The Dockerfile does not add Linux capabilities. It does not use
`--privileged` or `CAP_ADD`.

### PUID / PGID

`PUID` and `PGID` environment variables default to `1000` and remap
file ownership. This matches the pattern used by Linuxserver.io and
similar self-hosted images. Unraid users have different defaults
(`99` / `100`); see
[Install on Unraid](/docs/guides/installation/unraid#puid--pgid).

### Explicit non-root mode

When your container runtime starts the process as a non-root user
(via `user:` in Docker Compose or `securityContext.runAsUser` in
Kubernetes), the entrypoint detects this and skips PUID/PGID
remapping. The application runs directly as the specified UID/GID.

In this mode:

- `PUID` and `PGID` are ignored (a warning is logged when they
  differ from defaults).
- `/data` must be writable by the runtime UID/GID. For bind mounts,
  pre-create and `chown` the host directory. For Kubernetes, use
  `fsGroup`.
- `cap_drop: [ALL]` is safe because no privileged operations occur.
- Existing files (`houndarr.db`, `houndarr.db-wal`, `houndarr.db-shm`,
  `houndarr.masterkey`) must be owned by the runtime UID/GID. When
  migrating from the default compat mode, `chown -R` the data
  directory first.

The entrypoint validates `/data` permissions at startup and exits
with an actionable error message when the directory is not writable
or existing files are not accessible.

:::note[Rootless container engines]

This mode supports running the container process as non-root. It
does not guarantee compatibility with every rootless container
engine or storage backend combination. For rootless Podman, the
default compat mode with PUID/PGID already works via user namespace
mapping.

:::

### Health check

The Docker `HEALTHCHECK` polls `http://localhost:8877/api/health`
inside the container. The endpoint is intentionally unauthenticated
and returns only `{"status": "ok"}`.

## Deployment hardening checklist

- [ ] Run behind a reverse proxy with TLS termination
- [ ] Set `HOUNDARR_SECURE_COOKIES=true`
- [ ] Set `HOUNDARR_TRUSTED_PROXIES` to your proxy IP(s) or subnet(s)
- [ ] Do not expose port 8877 directly to the internet without a
      proxy
- [ ] Do not run with `HOUNDARR_DEV=true` in production (exposes
      Swagger UI at `/api/docs`)
- [ ] Back up the `/data` volume regularly (see
      [Backup and Restore](/docs/guides/backup-and-restore))
- [ ] Restrict file permissions on the data directory to the
      container user
- [ ] Keep the Docker image updated for security patches

When using proxy auth mode (`HOUNDARR_AUTH_MODE=proxy`):

- [ ] Set `HOUNDARR_TRUSTED_PROXIES` to your proxy's specific IP or
      subnet (avoid broad ranges like `0.0.0.0/0`)
- [ ] Confirm port 8877 is not reachable without going through the
      authenticating proxy (direct access bypasses SSO)
- [ ] Verify your proxy strips or overwrites the auth header from
      client requests before forwarding (all major SSO proxies do
      this by default)

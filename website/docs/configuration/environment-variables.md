---
sidebar_position: 1
title: Environment Variables
description: All environment variables supported by Houndarr.
---

# Environment Variables

Houndarr is configured primarily through environment variables set in your
`docker-compose.yml` or `docker run` command.

## Application settings

| Variable | Default | Description |
|----------|---------|-------------|
| `HOUNDARR_DATA_DIR` | `/data` | Directory for persistent data (SQLite DB and master key) |
| `HOUNDARR_HOST` | `0.0.0.0` | Host address to bind the web server to |
| `HOUNDARR_PORT` | `8877` | Port to bind the web server to |
| `HOUNDARR_DEV` | `false` | Enable development mode (auto-reload, API docs at `/api/docs`) |
| `HOUNDARR_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |

## Security settings

| Variable | Default | Description |
|----------|---------|-------------|
| `HOUNDARR_SECURE_COOKIES` | `false` | Set `Secure` flag on cookies (enable when behind HTTPS) |
| `HOUNDARR_COOKIE_SAMESITE` | `lax` | `SameSite` attribute for cookies: `lax` (allows dashboard links) or `strict` (blocks all cross-site requests) |
| `HOUNDARR_TRUSTED_PROXIES` | _(empty)_ | Comma-separated trusted proxy IPs or CIDR subnets (e.g. `10.0.0.1,172.18.0.0/16`); used for `X-Forwarded-For` and proxy auth |
| `HOUNDARR_AUTH_MODE` | `builtin` | Authentication method: `builtin` (default) or `proxy` (delegate to SSO reverse proxy) |
| `HOUNDARR_AUTH_PROXY_HEADER` | _(empty)_ | Header carrying the authenticated username in proxy mode (e.g. `Remote-User`, `X-authentik-username`); required when `AUTH_MODE=proxy` |

## Container settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `1000` | User ID for file ownership inside the container |
| `PGID` | `1000` | Group ID for file ownership inside the container |
| `TZ` | `UTC` | Container timezone (e.g. `America/New_York`) |

## Notes

### LXC / Proxmox / root-based hosts

If your Docker host runs containers as root (a common setup in Proxmox LXC
containers), set `PUID=0` and `PGID=0`. Houndarr will skip the privilege-drop
and run directly as root, matching the security posture of the rest of your
stack. A warning will be printed to stdout at startup as a reminder.

### Explicit non-root mode (`user:` / `runAsUser`)

If you start the container as a non-root user (via `user:` in Docker Compose
or `securityContext.runAsUser` in Kubernetes), `PUID` and `PGID` are
**ignored**. The entrypoint cannot remap file ownership without root, so it
skips remapping and runs the application directly as the specified UID/GID.

In this mode, `/data` must already be writable by the runtime user. For bind
mounts, pre-create and `chown` the host directory before starting the
container. If migrating from the default mode, `chown -R` all files in the
data directory (including `houndarr.db-wal` and `houndarr.db-shm`) to the
new UID/GID.

See [Trust & Security](/docs/security/trust-and-security#explicit-non-root-mode)
for details.

### Development mode

Setting `HOUNDARR_DEV=true` enables:

- Auto-reload on code changes
- FastAPI Swagger UI at `/api/docs`

:::warning
Do not run with `HOUNDARR_DEV=true` in production. It exposes the Swagger UI
which documents all API endpoints.
:::

### Secure cookies

Set `HOUNDARR_SECURE_COOKIES=true` when running behind a reverse proxy with
HTTPS termination. Without this, session cookies and login credentials are
transmitted in cleartext on the network.

See [Reverse Proxy](reverse-proxy.md) for the full configuration.

### Cookie SameSite policy

The default `HOUNDARR_COOKIE_SAMESITE=lax` allows session cookies to be sent
when you click a link to Houndarr from a dashboard app (Homepage, Homarr,
Organizr) or any external page. Without this, you would be redirected to the
login page despite having a valid session.

Set `HOUNDARR_COOKIE_SAMESITE=strict` if you prefer the most restrictive
cookie policy and do not access Houndarr via external links. State-changing
requests (POST, PUT, PATCH, DELETE) are protected by CSRF token validation
regardless of this setting.

### Proxy authentication mode

`HOUNDARR_AUTH_MODE=proxy` delegates authentication to an SSO reverse proxy
(Authelia, Authentik, oauth2-proxy, etc.). Requires both
`HOUNDARR_AUTH_PROXY_HEADER` and `HOUNDARR_TRUSTED_PROXIES`; the app refuses
to start without them. See [SSO proxy authentication](reverse-proxy.md#sso-proxy-authentication)
for setup instructions and examples.

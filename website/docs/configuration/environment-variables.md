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
| `HOUNDARR_TRUSTED_PROXIES` | _(empty)_ | Comma-separated trusted reverse-proxy IPs for `X-Forwarded-For` |

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

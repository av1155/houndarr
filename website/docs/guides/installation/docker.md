---
sidebar_position: 2
title: Docker
description: Pull the Houndarr Docker image and run it with docker run or Docker Compose.
---

# Install with Docker

The Docker image lives on GitHub Container Registry (GHCR) at
`ghcr.io/av1155/houndarr`.

## Docker (recommended)

Pull the latest image:

```bash
docker pull ghcr.io/av1155/houndarr:latest
```

Or pin to a specific version:

```bash
docker pull ghcr.io/av1155/houndarr:v1.0.8
```

Available architectures: `linux/amd64` and `linux/arm64`.

See [Install with Docker Compose](/docs/guides/installation/docker-compose)
for a complete Compose example, or
[Install from source](/docs/guides/installation/from-source) for the
Python development path.

## Container details

| Property | Value |
|----------|-------|
| Image | `ghcr.io/av1155/houndarr` |
| Default port | `8877` |
| Data volume | `/data` |
| Health check | `GET /api/health` |
| User | Non-root (`appuser`) after PUID/PGID remapping |

The container starts as root only to perform PUID/PGID file
ownership remapping, then drops to a non-root user via `gosu` before
starting the application. See
[Security Overview: Container security posture](/docs/security/overview#container-security-posture)
for the full trust model.

## Which *arr versions work?

Houndarr talks to your *arr instances through their REST APIs. For
the tested version matrix and Readarr-fork compatibility, see
[Compatibility](/docs/reference/compatibility).

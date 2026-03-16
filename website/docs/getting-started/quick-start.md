---
sidebar_position: 1
title: Quick Start
description: Get Houndarr running in under five minutes with Docker Compose.
---

# Quick Start

Get Houndarr running in under five minutes.

## Prerequisites

- Docker and Docker Compose installed on your host
- At least one running Sonarr or Radarr instance with an API key

## Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    container_name: houndarr
    restart: unless-stopped
    ports:
      - "8877:8877"
    volumes:
      - ./data:/data
    environment:
      - TZ=America/New_York
      - PUID=1000
      - PGID=1000
```

Then run:

```bash
docker compose up -d
```

Open `http://<your-host>:8877` in your browser. On first launch you will be
prompted to create an admin username and password.

## What happens next

1. Create your admin account on the setup screen.
2. Log in and go to **Settings**.
3. Add your Sonarr/Radarr instances (URL + API key).
4. Enable each instance — Houndarr begins searching on the configured schedule.

For more details, see [First-Run Setup](first-run-setup.md).

:::tip Good to know
Houndarr does not search your entire library — only items that Sonarr/Radarr report as missing or below your quality cutoff, in small batches. See [How Houndarr Works](/docs/concepts/how-houndarr-works) for details.
:::

## Using `docker run`

If you prefer `docker run` over Compose:

```bash
docker run -d \
  --name houndarr \
  --restart unless-stopped \
  -p 8877:8877 \
  -v /path/to/data:/data \
  -e TZ=America/New_York \
  -e PUID=1000 \
  -e PGID=1000 \
  ghcr.io/av1155/houndarr:latest
```

Replace `/path/to/data` with an absolute path on your host where Houndarr
should store its database and master key.

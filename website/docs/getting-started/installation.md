---
sidebar_position: 2
title: Installation
description: How to install Houndarr via Docker or from source.
---

# Installation

Houndarr is distributed as a Docker image published to GitHub Container Registry (GHCR).

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

See the [Quick Start](quick-start.md) for a complete Docker Compose example.

## Building from source

If you want to run Houndarr outside Docker or contribute to development:

```bash
# Clone the repository
git clone https://github.com/av1155/houndarr.git
cd houndarr

# Create a virtual environment
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

# Run in development mode
.venv/bin/python -m houndarr --data-dir ./data-dev --dev
```

The dev server will be available at `http://localhost:8877`.

### Requirements

- Python 3.12 or later
- pip

Development mode enables auto-reload and exposes the FastAPI Swagger UI at `/api/docs`.

## Container details

| Property | Value |
|----------|-------|
| Image | `ghcr.io/av1155/houndarr` |
| Default port | `8877` |
| Data volume | `/data` |
| Health check | `GET /api/health` |
| User | Non-root (`appuser`) after PUID/PGID remapping |

The container starts as root only to perform PUID/PGID file ownership remapping,
then drops to a non-root user via `gosu` before starting the application.

## Compatibility

Houndarr communicates with *arr instances through their REST APIs. The table below lists the versions tested against.

| Application | API version | Tested with |
|-------------|-------------|-------------|
| Radarr | v3 | 6.0.4.10291 |
| Sonarr | v3 | 4.0.17.2952 |
| Lidarr | v1 | 3.1.0.4875 |
| Readarr | v1 | 0.4.20.129 |
| Whisparr | v3 | 2.2.0.108 |

Any version that exposes the same API (v3 or v1 depending on the app) should work. When you test a connection, Houndarr reads the `appName` from the instance's system/status endpoint and verifies it matches the type you selected. If there is a mismatch, the test will tell you what the URL is actually running.

### Readarr forks

The original Readarr project was discontinued in June 2025. Community forks that use the same v1 API are expected to work when configured as type "Readarr":

- [Bookshelf](https://github.com/pennydreadful/bookshelf)
- [Reading Glasses](https://github.com/blampe/rreading-glasses)
- [Faustvii's Readarr](https://github.com/Faustvii/Readarr)

Forks that return an unrecognized `appName` in their system/status response will still connect; the type check only rejects known mismatches (e.g. pointing a "Radarr" config at a Sonarr URL).

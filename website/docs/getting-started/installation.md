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
docker pull ghcr.io/av1155/houndarr:v1.0.6
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

Development mode enables auto-reload and exposes the FastAPI Swagger UI at `/docs`.

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

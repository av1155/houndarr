---
sidebar_position: 6
title: From Source
description: Run Houndarr from a local Python checkout for development or contributor work.
---

# Install from Source

For contributor work, or to run Houndarr outside Docker, build from
a local Python checkout. End users should prefer
[Install with Docker Compose](/docs/guides/installation/docker-compose)
or [Install on Unraid](/docs/guides/installation/unraid); this page
is for development.

## Prerequisites

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/) (preferred) or pip
- Node.js 22 or later
- pnpm 11 or later (via `corepack enable`)

## Setup

```bash
# Clone the repository
git clone https://github.com/av1155/houndarr.git
cd houndarr

# Create the Python virtual environment and install runtime + dev deps.
# uv reads pyproject.toml + uv.lock and installs the PEP 735 `dev` group
# by default.
uv sync

# Compile the Tailwind + daisyUI CSS bundle
corepack enable
pnpm install --frozen-lockfile
pnpm run build-css

# Run in development mode
uv run houndarr --data-dir ./data-dev --dev
```

The dev server listens on `http://localhost:8877`.

## Rebuild the CSS bundle

Re-run `pnpm run build-css` after pulling commits that change
`src/houndarr/static/css/` or `src/houndarr/templates/`. Houndarr
refuses to start without the compiled bundle and prints the same
command in the log.

## Development mode

Passing `--dev` (or setting `HOUNDARR_DEV=true`) enables:

- Auto-reload on code changes
- The FastAPI Swagger UI at `/api/docs`

Do not run with `--dev` in production; Swagger exposes every
endpoint to unauthenticated readers.

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

## Requirements

- Python 3.12 or later
- pip
- Node.js 22 or later (only needed to compile the CSS bundle)
- pnpm via `corepack enable` (Node 20+ ships corepack)

## Setup

```bash
# Clone the repository
git clone https://github.com/av1155/houndarr.git
cd houndarr

# Create a Python virtual environment
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .

# Compile the Tailwind + daisyUI CSS bundle
corepack enable
pnpm install --frozen-lockfile
pnpm run build-css

# Run in development mode
.venv/bin/python -m houndarr --data-dir ./data-dev --dev
```

The dev server listens on `http://localhost:8877`.

The compiled `src/houndarr/static/css/app.built.css` is gitignored
because it is a build artefact. Re-run `pnpm run build-css` whenever
you pull commits that touch `src/houndarr/static/css/` or
`src/houndarr/templates/`. Houndarr refuses to start if the bundle is
missing and prints the exact command in the log.

The Docker image runs the same `pnpm run build-css` step
automatically as a multi-stage build, so Docker users do not need
Node or pnpm installed.

## Development mode

Passing `--dev` (or setting `HOUNDARR_DEV=true`) enables:

- Auto-reload on code changes
- The FastAPI Swagger UI at `/api/docs`

Do not run with `--dev` in production; Swagger exposes every
endpoint to unauthenticated readers.

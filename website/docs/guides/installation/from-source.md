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

## Setup

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

The dev server listens on `http://localhost:8877`.

## Development mode

Passing `--dev` (or setting `HOUNDARR_DEV=true`) enables:

- Auto-reload on code changes
- The FastAPI Swagger UI at `/api/docs`

Do not run with `--dev` in production; Swagger exposes every
endpoint to unauthenticated readers.

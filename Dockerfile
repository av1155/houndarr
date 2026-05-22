# SPDX-License-Identifier: AGPL-3.0-only
# SPDX-FileCopyrightText: 2026 Andrea A. Venti Fuentes
# syntax=docker/dockerfile:1
# =============================================================================
# Houndarr — production Docker image
# Base: python:3.13-slim (Debian bookworm slim)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: css-build
# Compile Tailwind v4 + Houndarr custom CSS into a single static file. Node
# lives only in this stage; the final runtime image stays Python-only.
# -----------------------------------------------------------------------------
FROM node:22-alpine AS css-build
WORKDIR /build

# Enable pnpm via corepack (bundled with Node 20+). The packageManager field
# in package.json pins the exact pnpm version.
RUN corepack enable

# Copy manifest + lockfile + workspace config first for better layer caching.
# pnpm 11 reads `allowBuilds` and `overrides` only from pnpm-workspace.yaml.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# Copy only the inputs Tailwind needs to scan and import.
COPY src/houndarr/static/css/ ./src/houndarr/static/css/
COPY src/houndarr/templates/ ./src/houndarr/templates/

RUN pnpm run build-css

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.13-slim

ARG HOUNDARR_VERSION=dev

# OCI labels
LABEL org.opencontainers.image.title="Houndarr" \
      org.opencontainers.image.description="Focused self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and Whisparr" \
      org.opencontainers.image.url="https://github.com/av1155/houndarr" \
      org.opencontainers.image.source="https://github.com/av1155/houndarr" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOUNDARR_VERSION="${HOUNDARR_VERSION}" \
    HOUNDARR_DATA_DIR=/data

WORKDIR /app

# Apply base-image security patches and install gosu for privilege dropping
# hadolint ignore=DL3008,DL3009
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends gosu curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying source (better layer caching).
# pyproject.toml + uv.lock are the single source of truth; uv exports a
# pinned requirements list at build time, no static requirements.txt needed.
# VERSION + hatch_build.py are required by the hatchling metadata hook that
# reads the project version; copy them too so `uv export` can resolve the
# build backend without the rest of the source tree.
COPY pyproject.toml uv.lock VERSION hatch_build.py ./
# hadolint ignore=DL3013
RUN pip install --no-cache-dir --upgrade pip uv \
    && uv export --frozen --no-hashes --no-emit-project --no-dev -o /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy application source
COPY src/ ./src/
COPY VERSION ./
COPY CHANGELOG.md ./

# Overlay the compiled Tailwind CSS from the css-build stage.
COPY --from=css-build /build/src/houndarr/static/css/app.built.css \
                      ./src/houndarr/static/css/app.built.css

# Create non-root runtime user and data directory
RUN groupadd -g 1000 appgroup \
    && useradd -u 1000 -g appgroup -m -s /sbin/nologin appuser \
    && mkdir -p /data \
    && chown -R appuser:appgroup /app /data

# Copy and make entrypoint executable
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose web UI port
EXPOSE 8877

# Data volume for persistent state
VOLUME ["/data"]

# Health check: poll the unauthenticated /api/health endpoint
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:8877/api/health || exit 1

# -----------------------------------------------------------------------------
# Privilege-drop strategy
# -----------------------------------------------------------------------------
# This image deliberately does NOT end with `USER appuser`. The image starts as
# root, then the entrypoint (`entrypoint.sh`) handles three modes:
#
#   1. Container started non-root (Kubernetes runAsUser, `docker run --user`):
#      preflights /data, then exec the app as the supplied UID.
#   2. PUID=0 (LXC/Proxmox where isolation is at the hypervisor level):
#      stays as root.
#   3. Default: remaps `appuser` to PUID/PGID, chowns /data, then drops
#      privileges via `gosu appuser` before exec-ing the app.
#
# Hard-coding `USER appuser` would silently break mode 3 (the most common one),
# because `id -u` would return 1000 and the entrypoint would skip the
# UID-remap branch. This is the canonical linuxserver.io PUID/PGID pattern
# used by hundreds of self-hosted images. See `entrypoint.sh` for details.
#
# Static analyzers (Semgrep `last-user-is-root`, etc.) flag the absence of a
# trailing `USER` directive without considering the entrypoint's runtime drop;
# the suppressions below acknowledge that and point reviewers to entrypoint.sh.
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root
ENTRYPOINT ["/entrypoint.sh"]
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root
CMD ["python", "-m", "houndarr", "--data-dir", "/data"]

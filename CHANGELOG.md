# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-03-17

### Added

- Lidarr (music), Readarr (books), and Whisparr support with dedicated API clients, search adapters, per-app search modes, and full UI integration (#200)

### Changed

- Database schema migrated to v5 with expanded instance-type constraints and per-app search-mode columns (#200)
- Search engine refactored around a unified adapter registry and `SearchCandidate` model, cutting `search_loop.py` by ~45% with zero behavior change (#200)
- Development status classifier updated to Production/Stable in `pyproject.toml` (#190)

---

## [1.0.8] - 2026-03-16

### Fixed

- In-app Settings Help page now links to the documentation website instead of a removed repository file (#181)
- Docker image pip upgraded at build time to patch CVE-2025-8869 and CVE-2026-1703 (#175)

---

## [1.0.7] - 2026-03-16

### Fixed

- Instance connection-test responses now escape HTML to prevent reflective XSS; exception details are no longer exposed in error responses (#168)
- Docker image base-layer packages are now upgraded at build time, patching CVE-2026-0861 (glibc integer overflow) (#170)

### Added

- Trivy filesystem scan, Trivy Docker image scan, and dependency-review PR check are now required CI checks (10 total, up from 7) (#170)

---

## [1.0.6] - 2026-03-15

### Fixed

- Connection errors now write exactly one `action="error"` log row per outage instead of one per retry, preventing the dashboard *24h errors* counter from inflating during startup races or service restarts (#140)
- A recovery `action="info"` row is now written to `search_log` when an unreachable instance becomes reachable again, making the recovery event visible on the Logs page (#140)
- A 10-second startup grace delay before the first search cycle gives co-located Sonarr/Radarr services time to become ready (#140)

---

## [1.0.5] - 2026-03-15

### Fixed

- Sonarr season-context missing search no longer re-searches the same season every cycle; cooldown and history are now keyed on a stable season identity instead of the rotating representative episode ID (#137)

---

## [1.0.4] - 2026-03-14

### Fixed

- Application `INFO`-level log messages (startup, search cycles, supervisor recovery) now appear in container stdout; previously only `WARNING`+ messages were visible because the root Python logger was never configured with the `--log-level` setting (#125)

---

## [1.0.3] - 2026-03-14

### Fixed

- Connection errors to `*arr` instances (e.g. during a cold-start race) now
  log at `WARNING` instead of `ERROR`, with a clear message including the
  instance name, URL, and retry interval (#119)
- The supervisor retries a failed connection every 30 seconds instead of
  waiting the full search interval, and logs an `INFO` recovery message once
  the instance is reachable again (#119)
- UI Logs page message for connection errors now reads `"Could not reach
  <url>"` instead of the raw internal error string (#119)

---

## [1.0.2] - 2026-03-14

### Fixed

- Logs copy button now works on plain HTTP (LAN/IP access), not just HTTPS or
  localhost; `navigator.clipboard` remains the primary path in secure contexts,
  with a `textarea` + `document.execCommand('copy')` fallback for non-secure
  contexts (#115)

---

## [1.0.1] - 2026-03-14

### Fixed

- `PUID=0`/`PGID=0` now runs the container directly as root without dropping
  privileges, fixing a startup `PermissionError` on Proxmox LXC and other
  root-based Docker hosts (#111)
- Clearer diagnostic message when `/data` is not writable, pointing to
  PUID/PGID misconfiguration as the cause

### Changed

- README: document `PUID=0`/`PGID=0` use case for LXC/Proxmox environments
- README: add `docker run` quick-start for users who prefer it over Compose

---

## [1.0.0] - 2026-03-14

First stable public release.

### Added

#### Core
- Automated missing-media search engine for Sonarr and Radarr instances
- Episode-level search for Sonarr (`EpisodeSearch` + `episodeIds`) with optional
  season-context mode (`SeasonSearch`)
- Movie-level search for Radarr (`MoviesSearch` + `movieIds`)
- Cutoff-unmet search: separate pass for items below quality profile cutoff
- Per-item cooldown to avoid re-searching recently searched items
- Per-instance hourly API cap to limit indexer load
- Bounded multi-page scanning with per-pass page/candidate limits to prevent
  backlog starvation
- Background supervisor: one `asyncio.Task` per enabled instance with graceful
  10-second shutdown

#### Web UI
- Dark-themed responsive web interface (FastAPI + Jinja2 + HTMX + Tailwind CSS CDN)
- Live dashboard with instance status cards, stats grid, and run-now buttons
- HTMX-driven partial updates (no full-page reloads after initial load)
- Persistent shell navigation with smooth content transitions
- Settings page with modal-based instance CRUD (add/edit/delete)
- Filterable, searchable log viewer with row limits (10 to All)
- Multi-format log copy/export: TSV, Markdown, JSON, plain text
- Cycle-grouped log display with summary statistics

#### Authentication and Security
- Single-admin username + bcrypt password authentication (cost 12)
- Signed session tokens via itsdangerous
- CSRF double-submit cookie protection on all mutating endpoints
- Login brute-force rate limiter
- Fernet encryption for stored API keys (master key auto-generated on first run)
- SSRF guard: instance URL validation blocks localhost, loopback, link-local,
  and unspecified targets; allows RFC-1918 private ranges for Docker/LAN use
- Secure cookie support for HTTPS deployments (`HOUNDARR_SECURE_COOKIES`)
- Trusted proxy support for accurate client-IP detection (`HOUNDARR_TRUSTED_PROXIES`)
- API key masking in UI (sentinel `__UNCHANGED__` pattern)

#### Infrastructure
- Single-container Docker deployment (`python:3.12-slim`, `gosu` for PUID/PGID)
- Multi-arch container builds (amd64/arm64) via GitHub Actions
- Automated GHCR publishing on version tags
- Docker HEALTHCHECK via `/api/health` endpoint
- SQLite database via aiosqlite (schema version 4, auto-migration on startup)
- Log retention: startup purge plus periodic uptime purge of stale search log rows
- Click CLI with environment variable support for all configuration options

#### CI/CD
- Ruff linting and formatting checks
- mypy strict type checking
- Bandit SAST scanning
- pip-audit dependency vulnerability scanning
- Hadolint Dockerfile linting
- actionlint workflow linting
- pytest test suite (303 tests) with pytest-asyncio and respx HTTP mocking

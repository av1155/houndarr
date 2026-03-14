# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

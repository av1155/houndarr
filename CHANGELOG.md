# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

---

## [1.11.0] - 2026-04-29

### Added

- `HOUNDARR_LOG_RETENTION_DAYS` env var configures the search log retention threshold (default `30`; `0` disables automatic purges, `7` to `365` overrides the default). (#586)
- Helm chart ships a `startupProbe` with a 10-minute default budget so first-boot migrations on Kubernetes finish before the liveness probe kicks in; `failureThreshold` and `periodSeconds` are exposed in `values.yaml`. (#586)

### Changed

- Dashboard aggregations cache for 20 seconds in-process so polling tabs share one DB scan; mutation routes invalidate immediately, and the HTMX poll drops new triggers while a previous response is still in flight. (#586)
- SQLite connections are reused through a connection pool with the modern operational PRAGMA stack (`synchronous=NORMAL`, `temp_store=MEMORY`, larger per-connection cache, memory-mapped reads, capped WAL growth); `PRAGMA optimize` runs after migrations and after each daily retention pass. (#586)

### Fixed

- From-source installs now refuse to start with a clear error message when the compiled `app.built.css` is missing instead of silently 404-ing every stylesheet request, and the from-source install guide documents the `pnpm install` and `pnpm run build-css` step required since `1.10.0`. (#582)
- Dashboard no longer hangs on databases with large `search_log` tables: two new composite indexes serve the v14 cooldown back-fill and the dashboard aggregation queries instead of full-table scans, and the back-fill itself only touches cooldown rows still at the default `'missing'` stamp. (#586)

---

## [1.10.1] - 2026-04-29

### Changed

- `/static/*` assets are returned with `Cache-Control: public, max-age=31536000, immutable` so browsers skip revalidation between releases. (#579)

### Fixed

- Changelog modal and add-instance settings modal cap to a sensible desktop width again, restoring the limit lost from the production CSS bundle by the `1.10.0` macro refactor. (#574)
- HTML responses set `Cache-Control: no-cache` and `Vary: HX-Request`, so browsers pick up new asset paths after an upgrade without a manual hard reload and HTMX partials no longer share a browser-cache entry with full pages. (#579)

---

## [1.10.0] - 2026-04-28

### Added

- Per-instance `upgrade_series_window_size` setting (default `5`, range `1-100`) for Sonarr and Whisparr v2 tunes the upgrade pool series window; existing instances pick up the default at migration time. (#495)
- Dashboard counters auto-refresh every 10 minutes via a supervisor snapshot loop, displaying per-instance monitored and unreleased totals without polling each *arr on every status request. (#461)
- Per-instance hourly budget meter, search-kind icons on `Recent hunts` and `Cooldown schedule` rows, live next-patrol countdown anchored on supervisor cycle-end signals, stale-snapshot pill when monitor data exceeds 15 minutes, multi-instance error banner, and tooltips on the headline stats. (#503)
- Settings page `Admin` dropdown adds Maintenance (`clear logs`) and Danger (`factory reset`) controls, and reorganises password and changelog preferences into the same collapsible surface. (#475)
- Caps Lock badge on password fields appears when Caps Lock is active or when the field receives focus while Caps Lock is already on. (#499)
- Opt-in GitHub release check in the Updates panel (disabled by default) surfaces a link to the latest release on GitHub when a newer version is available. (#497)

### Changed

- Dashboard redesigned with per-instance cards displaying `WANTED`, `ELIGIBLE`, and `SEARCHED` stats, a `Cooldown schedule` inset, type-colored policy chips, and per-instance error banners with deep links to filtered logs. (#501)
- Logs page rewritten from a 10-column table into a `cycle-card` stream with cursor pagination, multi-select instance filter, hide-skipped toggle, head-poll banner, and silent auto-refresh when scrolled to top. (#505)
- Login and setup pages redesigned with a Station auth card (logo topbar, eyebrow, accented headings, single-column form, version footer). (#499)
- Random search order now uses a stratified-shuffle page deck plus partial-page sentinel padding, so per-page and per-item dispatch probability stays uniform across the backlog without wrap-once or last-page over-selection. (#491)
- Hourly rate-limit skip rows now read `hourly limit reached (N/hr)` across missing, cutoff, and upgrade passes. (#491)
- Cooldown rows stamp their `search_kind` (missing, cutoff, upgrade) at insert time and a supervisor reconcile step prunes cooldowns whose items are no longer wanted on the *arr side, restoring accurate dashboard breakdown counts. (#463)
- Build pipeline compiles Tailwind v4 and daisyUI v5 locally at Docker build time, eliminating the Tailwind play CDN and the `unpkg` htmx script. (#481)
- Unified `status-dot`, `status-pill`, and `station-tooltip` components with a cyan accent band and accessible focus states across dashboard, settings, and instance forms. (#487)
- Schema v16 disambiguates Whisparr v2 from the product family: an idempotent migration renames `whisparr_search_mode` and `upgrade_whisparr_search_mode` columns and rewrites stored `whisparr_episode` item-type values to their `whisparr_v2_*` counterparts; existing instances retain settings and id. (#493)

### Fixed

- Database upgrade safety: version-locked migration `CHECK` constants prevent `init_db` from crashing on v1.7.0-v1.9.0 deployments with Whisparr v2 instances, and the v10 migration now commits before disabling foreign keys to stop a `CASCADE` that wiped cooldowns on v1.0.x to v1.7.0+ upgrades. (#559)
- Supervisor catches both `httpx.TransportError` and `ClientTransportError` on snapshot-refresh, reconcile-fetch, and scheduled-cycle paths, so transport failures log at `WARNING` with the retry signal instead of producing full tracebacks. (#561)
- Hardened HTTP redirect handling and external repository URLs: a `_redirect_guard` event hook blocks redirects to loopback and link-local addresses, proxy username echo uses `hmac.compare_digest`, and GitHub release URLs and `HOUNDARR_UPDATE_CHECK_REPO` are validated with safe fallback. (#507)
- Curated `InstanceValidationError.public_message` text replaces the raw exception in instance validation banners, so chained exception details no longer leak through the response. (#517)
- Password field handlers no longer break after HTMX `outerHTML` swaps, and the Caps Lock badge surfaces on focus when Caps Lock is already engaged. (#564)
- Cooldown schedule entries on dashboard cards anchor to the top of the card instead of floating vertically centered, eliminating empty rows when adjacent cards have differing heights. (#549)
- Cycle left-edge accent bar stays visible when hovering log entries. (#547)
- Logs page distinguishes a fresh install (`No log entries yet`) from a filter that matches nothing (`No entries match those filters.`). (#566)
- Removed duplicate copy in the Changelog notifications section on the Settings page that paraphrased the toggle label. (#422)

### Removed

- Dropped the in-app `/settings/changelog/full` page in favour of a `Latest on GitHub` link to the repository `CHANGELOG.md`. (#497)

---

## [1.9.0] - 2026-04-18

### Added

- Houndarr now has a [Discord server](https://discord.gg/t29AhAarYk) for questions and discussion. (#411)
- `What's New` modal surfaces every CHANGELOG block between your last-seen and the running version on first load after an update, with older entries collapsed into an accordion. (#409)
- `Reset to Defaults` button in the instance settings modal restores every search setting for an instance type. (#405)
- Per-instance search order: `random` (new default for newly added instances) picks a random page of the wanted list each cycle and shuffles items within it, so coverage spreads across the backlog over time; `chronological` walks oldest-first. (#395)

### Changed

- Documentation site rebuilt on the [Diataxis](https://diataxis.fr/) structure with a Technical Paper light-mode redesign, expanded coverage (Unraid install guide, `your-first-cycle` tutorial, 4-page security section), offline search, click-to-zoom images, and rendered Mermaid diagrams. (#399, #401)

### Fixed

- Invalid time-window strings now report a friendly error instead of surfacing the underlying `ValueError` message to the UI. (#407)

---

## [1.8.0] - 2026-04-16

### Added

- Per-instance `Allowed Search Window` setting restricts scheduled cycles to one or more `HH:MM-HH:MM` time-of-day windows using container local time; blank preserves 24/7 behaviour and `Run Now` bypasses the gate. (#388)

### Fixed

- Instance URLs using `host.docker.internal` and `host.containers.internal` (the Docker/Podman container-to-host aliases) are now accepted by the SSRF guard. (#387)
- Helm chart `appVersion` is now prefixed with `v` so it matches the published Docker image tags. (#364)

---

## [1.7.0] - 2026-04-04

### Added

- Whisparr v3 instance type for the Radarr-based Whisparr application, with client-side library filtering and `MoviesSearch` dispatch. (#338)
- Version-based mismatch detection in the connection test warns when the selected Whisparr version does not match the remote instance. (#338)
- Schema migration v10 automatically renames existing `whisparr` instances to `whisparr_v2` on upgrade. (#338)

### Fixed

- Whisparr v2 `releaseDate` parsing now handles the ISO date string format returned by the real API, restoring post-release grace period checks. (#339)

---

## [1.6.6] - 2026-04-02

### Added

- Connection testing now verifies the remote application type matches the selected instance type, preventing misconfiguration (e.g. a Sonarr URL saved as Radarr). (#333)
- Compatibility table in the installation docs listing tested *arr versions and Readarr fork support. (#333)

### Fixed

- CLI flags like `--data-dir` are now correctly propagated when running in dev mode with auto-reload. (#333)
- Connection test success and error messages are no longer overwritten by generic text after 80ms. (#333)

---

## [1.6.5] - 2026-04-01

### Fixed

- Accessing Houndarr via links from dashboard apps (Homepage, Homarr, Organizr) no longer redirects to `/login`; session cookies now default to `SameSite=Lax` instead of `Strict` (#318).

### Added

- `HOUNDARR_COOKIE_SAMESITE` environment variable to configure the `SameSite` cookie policy; accepts `lax` (default) or `strict` (#318).

---

## [1.6.4] - 2026-03-31

### Fixed

- Password change form no longer returns a raw 422 error; the form now submits via HTMX, matching every other mutation in the app (#311).

### Changed

- Idle CPU usage reduced significantly by replacing the Python subprocess Docker HEALTHCHECK with `curl`, consolidating `/api/status` from 13 database connections per poll down to 1, caching the setup-complete check and Fernet encryption object, and increasing the dashboard poll interval from 15s to 30s (#312).

---

## [1.6.3] - 2026-03-26

### Fixed

- Missing and cutoff search passes now rotate their starting page each cycle; previously they always began at page 1, so items beyond the initial window were never reached when earlier items were on cooldown (#294).

---

## [1.6.2] - 2026-03-22

### Fixed

- Instance URLs now accept underscores in hostnames alongside hyphens and dots (e.g. `http://radarr_hd:7878`); underscore-named containers were previously rejected as invalid (#285).
- Whisparr records with no series linkage, which previously caused repeated search errors labeled `Unknown Series - S00` in the logs, are now skipped instead of producing a failed search entry (#286).

---

## [1.6.1] - 2026-03-21

### Changed

- Docker image OCI license metadata now reports `AGPL-3.0-only`, and the project license is now GNU AGPLv3 (#275).

---

## [1.6.0] - 2026-03-21

### Added

- Opt-in upgrade search pass that periodically re-searches library items which already have a file and meet the quality cutoff, giving each `*arr` instance a chance to find better releases; each instance has independent batch size, cooldown, and hourly cap controls (#266).

### Changed

- A 3-second pause is now inserted between consecutive real searches within the same cycle to spread downstream indexer fan-out; the delay applies only to dispatched searches, not to skipped or errored items (#272).

### Fixed

- Navigating to the settings help page via the "What do these settings mean?" link inside the instance modal no longer leaves the page scroll-locked until refresh (#268).
- Instance modal on mobile no longer briefly appears compact before expanding; the dialog now animates in fully populated (#268).
- Dashboard instance cards now enter with a smooth container-level fade that matches the shell animation instead of a per-card flash (#268).

---

## [1.5.0] - 2026-03-21

### Added

- Non-root container startup is now supported via pod `securityContext` for clusters enforcing Pod Security Standards, as an alternative to the default `PUID`/`PGID` remapping mode (#258).
- Proxy authentication mode (`HOUNDARR_AUTH_MODE=proxy`) delegates login to an upstream identity-aware proxy via a configurable request header (#259).
- Official Helm chart published to `oci://ghcr.io/av1155/charts/houndarr`; Flux users can deploy with an `OCIRepository` + `HelmRelease` instead of raw manifests (#261).

---

## [1.4.0] - 2026-03-21

### Added

- `HOUNDARR_TRUSTED_PROXIES` now accepts CIDR subnets (e.g. `172.18.0.0/16`) in addition to individual IP addresses (#245, #248)
- Kubernetes deployment guide with StatefulSet, headless Service, and Ingress examples (#255)
- FAQ entry explaining why Houndarr exists alongside built-in *arr search (#253)

---

## [1.3.2] - 2026-03-20

### Fixed

- Add/Edit instance modal no longer collapses to a near-zero-height sliver on iOS Safari and Chrome for iOS; replaced flex-based height with a direct `dvh` constraint on the content div (#241).

---

## [1.3.1] - 2026-03-20

### Fixed

- All form inputs and selects now use 16px font size, preventing iOS Safari from auto-zooming on focus across the login, setup, instance form, and log filter controls (#234)
- Add/Edit instance modal now scrolls fully on mobile; the Cancel button was unreachable due to a missing height constraint on the dialog element (#234)
- Instance modal no longer auto-focuses the help link on open; the close button receives focus instead (#234)
- Username `pattern` attribute corrected from `[A-Za-z0-9_.-]+` to `[-A-Za-z0-9_.]+` to fix an invalid descending character range that caused a console error in Chrome (#234)
- Logs page Reason/Message column no longer hidden at mid-range viewport widths (768–1102px); table layout breakpoint raised to 1100px (#234)

### Changed

- Supervisor staggers instance startup by 30 seconds per instance so scheduled cycles no longer all fire simultaneously on first run (#235)

---

## [1.3.0] - 2026-03-20

### Added

- Missing-pass cycles now retry an item on the first eligible pass after its release date when the previous skip was caused by a release-timing block, avoiding unnecessary cooldown waits (#226)

### Fixed

- Log cycles from concurrent instances no longer interleave; all rows for a given cycle now appear contiguously even when multiple instances run at the same second (#230)

### Changed

- Station dark theme gains ambient depth on desktop: faint radial glows on the page body, a subtle horizontal line texture, and a soft cyan halo below the navbar (#230)
- App shell and templates updated for consistent Station design system layout with improved desktop and mobile parity (#228, #230)

---

## [1.2.1] - 2026-03-19

### Fixed

- `Last hour` metric on dashboard instance cards now counts only the trailing 60-minute window; the previous `datetime()` string comparison mis-matched ISO-8601 timestamps, causing the value to equal the daily total (#222)
- Radarr troubleshooting instructions corrected from `Movies → Discover` to `Wanted → Missing` / `Wanted → Cutoff Unmet` (#222)

### Changed

- Dashboard top section replaced with a focused three-metric strip (Searched, Skipped, Errors over 24 h) with semantic color accents; Fleet Summary and Auto-refresh cards removed (#222)
- Documentation screenshots refreshed and added to pages that previously had none; homepage screenshot gallery redesigned with a full-width hero and supporting grid (#222)

---

## [1.2.0] - 2026-03-18

### Added

- Optional per-instance download-queue backpressure gate skips the entire search cycle when the queue meets or exceeds a configurable limit (#216)

### Fixed

- Missing-pass starvation when all page-one candidates are on cooldown; the search loop now scans up to five wanted-list pages per pass (#214)
- Replaced the monolithic `unreleased_delay_hrs` (default 36h) with a non-configurable pre-release gate and a separate `post_release_grace_hrs` (default 6h), so truly unreleased items are always blocked while recently-released items clear faster (#214)
- Existing instances with the old 36h default are migrated to 6h; custom values are preserved (#214)

### Changed

- Database schema migrated to v7 with `post_release_grace_hrs` replacing `unreleased_delay_hrs` (v6) and new `queue_limit` column (v7) (#214, #216)
- Documentation updated across AGENTS.md, in-app settings help, website docs, and README to reflect post-release grace, queue backpressure, and updated log reason strings (#218)

---

## [1.1.1] - 2026-03-17

### Fixed

- Settings help panel now describes search modes for all four apps that support them (Sonarr, Lidarr, Readarr, Whisparr) instead of Sonarr only (#210)
- All *arr app listings across code, UI, docs, and website now use the canonical order: Radarr, Sonarr, Lidarr, Readarr, Whisparr (#210)
- Bug report template version field expanded from "Sonarr / Radarr" to include all five apps (#210)

---

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
- A 10-second startup grace delay before the first search cycle gives co-located *arr services time to become ready (#140)

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
- Automated missing-media search engine for Radarr and Sonarr instances
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

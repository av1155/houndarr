---
name: houndarr-architecture
description: Houndarr's source layout and architectural patterns at file granularity. Loads when reading or editing src/houndarr/. Covers the per-file purpose for auth/, clients/, engine/, routes/, services/; the wire-models vs domain-models split; the *arr API spec snapshots under docs/api/; and pointers to the more specific database / engine skills for narrower scopes.
paths:
  - "src/houndarr/**"
---

# Houndarr architecture reference

For narrower scopes:

- Database schema and migrations: see `houndarr-database` skill (loads on
  `src/houndarr/database.py`).
- Algorithmic verification: see `verify-algorithms` skill (loads on
  `src/houndarr/engine/**`).

## Source layout

```
src/houndarr/
  __main__.py          # CLI entry point (Click), logging setup, uvicorn.run
  app.py               # create_app(), lifespan, middleware registration
  auth/                # AuthMiddleware, bcrypt, CSRF, rate limiter (seam package)
    password.py        # bcrypt verify / hash helpers
    rate_limit.py      # in-memory login rate limiter
    session.py         # signed session cookie encode / decode
    setup.py           # first-run admin setup + password policy
    csrf.py            # CSRF double-submit token rotation
    proxy_auth.py      # reverse-proxy trust gate and header extraction
    identity.py        # current-user resolution from session or proxy header
    middleware.py      # AuthMiddleware dispatch (builtin vs proxy path)
  config.py            # AppSettings dataclass, get_settings() singleton
  crypto.py            # Fernet encrypt/decrypt, master key management
  database.py          # get_db() context manager, schema migrations
  enums.py             # StrEnum consolidation (SearchKind, SearchAction, CycleTrigger, ItemType)
  errors.py            # HoundarrError hierarchy (Client/Engine/Service/Route)
  value_objects.py     # Frozen value objects shared across layers (ItemRef)
  clients/             # httpx-based *arr API clients
    base.py            # ArrClient ABC with _get()/_post() + raise_for_status() + get_queue_status()
    sonarr.py          # SonarrClient (episode/season search, v3 API)
    radarr.py          # RadarrClient (movie search, v3 API)
    lidarr.py          # LidarrClient (album/artist search, v1 API)
    readarr.py         # ReadarrClient (book/author search, v1 API)
    whisparr_v2.py     # WhisparrV2Client (Sonarr-based, episode/season search)
    whisparr_v3.py     # WhisparrV3Client (v3, Radarr-based, movie/scene search)
  engine/
    candidates.py      # SearchCandidate dataclass, ItemType re-export, date helpers
    search_loop.py     # run_instance_search(): unified search pipeline (missing/cutoff/upgrade passes, queue-backpressure gate)
    supervisor.py      # Supervisor: one asyncio.Task per enabled instance
    adapters/
      __init__.py      # AppAdapter dataclass, ADAPTERS registry, get_adapter()
      protocols.py     # AppAdapterProto: runtime_checkable Protocol matching the AppAdapter shape
      sonarr.py        # Sonarr adapter: candidate conversion + dispatch
      radarr.py        # Radarr adapter: candidate conversion + dispatch
      lidarr.py        # Lidarr adapter: candidate conversion + dispatch
      readarr.py       # Readarr adapter: candidate conversion + dispatch
      whisparr_v2.py   # Whisparr v2 adapter: candidate conversion + dispatch
      whisparr_v3.py   # Whisparr v3 adapter: movie/scene candidate conversion + dispatch
  routes/
    _htmx.py           # is_hx_request() shared helper for partial vs full renders
    pages.py           # Setup, Login, Dashboard, Logs, Settings page routes
    health.py          # GET /api/health (Docker HEALTHCHECK)
    settings/          # Settings surface split by concern
      __init__.py      # composes the sub-routers into a single settings_router
      _helpers.py      # template render, client build, connection check, validators
      page.py          # GET /settings
      account.py       # POST /settings/account/password
      instances.py     # /settings/instances/* (CRUD, test-connection, toggle)
    api/
      logs.py          # GET /api/logs (JSON, with cursor-based pagination)
      status.py        # GET /api/status (JSON, dashboard polling)
  services/
    instances.py       # Instance CRUD, InstanceType StrEnum
    cooldown.py        # Per-item search cooldown tracking
    url_validation.py  # SSRF guard for instance URLs
```

## Wire models vs domain models

- **Wire models** (`clients/_wire_models/`): every *arr HTTP response is
  validated with a Pydantic model from this package before it reaches a
  parser. `PaginatedResponse[T]` (generic, PEP 695 syntax) covers the
  shared `/wanted/*` envelope; `SystemStatus` and `QueueStatus` back
  `ArrClient.ping()` and `ArrClient.get_queue_status()`; per-app
  `*WantedEpisode` / `*WantedMovie` / `*WantedAlbum` / `*WantedBook`
  and `*LibraryEpisode` / `*LibraryMovie` / `*LibraryAlbum` / `*LibraryBook`
  models name the record shapes. `ArrSeries` / `ArrArtist` / `ArrAuthor`
  type the parent-aggregate fetches. All wire models extend an internal
  `_ArrModel` that sets `populate_by_name=True` + `extra="ignore"` so
  unknown fields from new *arr versions never raise. Field names are
  snake_case in Python and alias to the camelCase the APIs serialise.
- **Domain models** (parsed result types): `MissingEpisode`,
  `LibraryMovie`, etc. are frozen dataclasses, one per client file
  next to the client that builds them. Every frozen dataclass uses
  `slots=True`. `Instance` composes seven frozen sub-structs (`core`,
  `missing`, `cutoff`, `upgrade`, `schedule`, `snapshot`, `timestamps`)
  and is itself frozen and slotted; callers evolve it through
  `dataclasses.replace`. `AppSettings` is the only deliberately-mutable
  dataclass (env overrides applied in-place on the lazy singleton).

## Auth composition

Global `AuthMiddleware` (Starlette `BaseHTTPMiddleware`) routes every
request through one of three path buckets; no per-route auth decorators:

- `_API_KEY_PATHS` (currently `/api/v1/widget`): the top-level
  `dispatch()` sends the request straight to `_dispatch_api_key`,
  which verifies `X-Api-Key` against the `widget_api_key` table
  (constant-time compare on the SHA-256 digest) and applies a
  per-IP attempt rate limit. Bypasses session and CSRF.
- `_PUBLIC_PATHS` (`/setup`, `/login`, `/api/health`, `/static`): no
  auth. Each of `_dispatch_builtin` and `_dispatch_proxy`
  short-circuits these before any session or proxy-header check.
- Everything else: mode-dependent. `_dispatch_builtin` enforces the
  session cookie + CSRF (default); `_dispatch_proxy` enforces the
  proxy-trust gate + CSRF when `HOUNDARR_AUTH_MODE=proxy`.

Proxy-auth trust and header reads flow through two primitives in
`auth.py`: `_is_trusted_proxy(request)` (IP gate) and
`_extract_proxy_username(request)` (header read, assumes trust already
verified). The middleware's `_dispatch_proxy` and the standalone
`_validate_proxy_auth` both compose these so the gate logic lives in
one place.

## Encryption

Master key in `request.app.state.master_key`; passed explicitly to
service functions as `master_key=` kwarg; never imported globally.

## HTMX

SPA-like shell navigation; nav links use `hx-target="#app-content"`
with `hx-swap="innerHTML"` and `hx-push-url="true"`. Routes check
`is_hx_request(request)` from `routes/_htmx.py` and return either
partial or full template. Templates are lazily initialised via a
module-level singleton.

## Supervisor

One `asyncio.Task` per enabled instance; 10s shutdown timeout.

## search_log

Every search attempt writes a row with action
`searched` / `skipped` / `error` / `info`.

## *arr API reference (local)

Full upstream OpenAPI specs vendored under `docs/api/` (one per app:
sonarr, radarr, whisparr_v2, whisparr_v3, lidarr, readarr).
**Source of truth** when touching `clients/` code; see
`docs/api/README.md`. Refreshed weekly (Mon 10:00 UTC) by
`api-snapshot-refresh.yml`, so specs are never more than a week stale.

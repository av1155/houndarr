# Architecture Plan: Search Engine Refactor + Queue-Awareness

> **Status:** Draft reference for future implementation
> **Created:** 2026-03-16
> **Base commit:** `50eed12` (v1.0.8, main)
> **Branch:** `feat/search-engine-refactor-queue-awareness`

This document captures a thorough analysis of Houndarr's current search
engine architecture, identifies what should change, and provides a concrete
implementation plan for two goals:

1. **Refactor** the search engine to eliminate internal duplication and
   create cleaner boundaries between engine logic and app-specific logic
2. **Add queue-awareness** so Houndarr skips items already in the download
   queue before triggering redundant searches

The analysis includes security review, future-app compatibility analysis
(Whisparr, Lidarr, Readarr), and risk assessment.

### Sources

**Local API specs (vendored in `docs/api/`, auto-refreshed weekly):**

- `docs/api/sonarr_openapi.json` — Sonarr v3 API
- `docs/api/radarr_openapi.json` — Radarr v3 API
- `docs/api/whisparr_openapi.json` — Whisparr v3 API
- `docs/api/lidarr_openapi.json` — Lidarr v1 API
- `docs/api/readarr_openapi.json` — Readarr v1 API

**Upstream source URLs:**

- Sonarr: <https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json>
- Radarr: <https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json>
- Whisparr: <https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/Whisparr.Api.V3/openapi.json>
- Lidarr: <https://raw.githubusercontent.com/lidarr/Lidarr/develop/src/Lidarr.Api.V1/openapi.json>
- Readarr: <https://raw.githubusercontent.com/Readarr/Readarr/develop/src/Readarr.Api.V1/openapi.json>

---

## Table of Contents

- [A. Current Architecture Findings](#a-current-architecture-findings)
- [B. Current Hardcoded Assumptions](#b-current-hardcoded-assumptions)
- [C. Security-Sensitive Boundaries](#c-security-sensitive-boundaries)
- [D. Architecture Options Considered](#d-architecture-options-considered)
- [E. Recommended Design Direction](#e-recommended-design-direction)
- [F. Reusable vs App-Specific](#f-reusable-vs-app-specific)
- [G. Future-App Compatibility Analysis](#g-future-app-compatibility-analysis)
- [H. Compatibility and Non-Breaking Analysis](#h-compatibility-and-non-breaking-analysis)
- [I. Recommended Sequencing](#i-recommended-sequencing)
- [J. API-Doc and Workflow Recommendation](#j-api-doc-and-workflow-recommendation)
- [K. Step-by-Step Implementation Plan](#k-step-by-step-implementation-plan)
- [L. Risks and Tradeoffs](#l-risks-and-tradeoffs)
- [M. Queue-Awareness Bridge Plan](#m-queue-awareness-bridge-plan)
- [N. Final Recommendation](#n-final-recommendation)
- [Appendix 1: Complete Search Flow Map](#appendix-1-complete-search-flow-map)
- [Appendix 2: Complete sonarr/radarr Reference Inventory](#appendix-2-complete-sonarrradarr-reference-inventory)
- [Appendix 3: Upstream API Compatibility Matrix](#appendix-3-upstream-api-compatibility-matrix)
- [Appendix 4: Database Schema Reference](#appendix-4-database-schema-reference)
- [Appendix 5: Test Coupling Analysis](#appendix-5-test-coupling-analysis)
- [Appendix 6: Security Boundary Map](#appendix-6-security-boundary-map)
- [Appendix 7: Existing Queue-Related Code](#appendix-7-existing-queue-related-code)

---

## A. Current Architecture Findings

### End-to-end search flow

The search cycle follows a clean pipeline: **Supervisor** ->
`run_instance_search()` -> **client fetch** -> **eligibility pipeline** ->
**search dispatch** -> **record + log**.

1. **Supervisor** (`engine/supervisor.py`, 362 lines) is fully
   type-agnostic. It manages one `asyncio.Task` per enabled instance,
   handles connection-retry state machines, and delegates entirely to
   `run_instance_search()`. No sonarr/radarr branching exists here.

2. **`run_instance_search()`** (`engine/search_loop.py:255-493`) is where
   all type-specific logic concentrates. It:
   - Constructs the correct client based on `instance.type` (line 293-299)
   - Runs a **missing pass**: pages through `client.get_missing()`, applies
     eligibility checks (unreleased delay, hourly cap, cooldown), dispatches
     search commands
   - Runs a **cutoff pass** via `_run_cutoff_pass()` if enabled

3. **Clients** (`clients/sonarr.py`, `clients/radarr.py`) extend
   `ArrClient(ABC)` which provides `_get()`/`_post()` with
   `raise_for_status()`. Each client returns its own frozen dataclass
   (`MissingEpisode`, `MissingMovie`).

4. **Cooldown** (`services/cooldown.py`) tracks
   per-`(instance_id, item_id, item_type)` search timestamps. Hourly cap
   is counted from `search_log`, not `cooldowns`.

5. **Logging** (`_write_log`) inserts rows into `search_log` with action,
   item identity, search_kind, cycle tracking, and reason strings.

### Architecture quality assessment

The supervisor is well-designed and already generic. The client base class
is clean. The pain point is `search_loop.py` (798 lines), which contains:

- All type-dispatching logic
- All eligibility checks
- All label generation
- All unreleased-date logic
- The entire cutoff pass duplicated across two `if/else` branches (~260
  lines of parallel code at lines 534-795)

### Key design observations

1. **Client lifecycle**: The search loop creates a NEW client context
   manager for each search command (lines 427-444), even though one is
   already open for the `get_missing` call. Each search POST uses a fresh
   httpx connection.

2. **Hourly cap is per search_kind**: Missing and cutoff passes track their
   hourly caps independently via `_count_searches_last_hour(instance_id,
   search_kind)` which queries `search_log` with `search_kind` filter.

3. **Cooldown is NOT per search_kind**: The `is_on_cooldown` function
   queries the `cooldowns` table which has no `search_kind` column.
   However, in practice missing items and cutoff-unmet items are disjoint
   sets.

4. **Season-context mode uses synthetic negative IDs**:
   `_season_item_id(series_id, season) = -(series_id * 1000 +
   season_number)`. Stored in both `cooldowns.item_id` and
   `search_log.item_id`.

5. **Cutoff pass for Sonarr does NOT support season_context**: Even if
   `sonarr_search_mode == season_context`, the cutoff pass always does
   episode-level search.

6. **`_write_log` is exported**: Imported and called directly by the
   supervisor for system-level messages.

7. **`master_key` parameter in `run_instance_search`**: Accepted in the
   signature but unused -- the `Instance` already has the decrypted
   `api_key`.

---

## B. Current Hardcoded Assumptions

### Type enumeration (exactly two types)

| Location | Constraint |
|---|---|
| `InstanceType(StrEnum)`: `sonarr`, `radarr` | Python enum in `services/instances.py:28-32` |
| `instances.type CHECK(type IN ('sonarr', 'radarr'))` | DB schema in `database.py:30` |
| `cooldowns.item_type CHECK(item_type IN ('episode', 'movie'))` | DB schema |
| `search_log.item_type CHECK(item_type IN ('episode', 'movie'))` | DB schema |
| `ItemType = Literal["episode", "movie"]` | Python type in `cooldown.py` and `search_loop.py` |

### Binary `if/else` branching (5 locations in engine/routes)

| # | File:Line | What it decides |
|---|---|---|
| 1 | `search_loop.py:293` | Client construction: `if sonarr ... else RadarrClient` |
| 2 | `search_loop.py:330` | `isinstance(item, MissingEpisode)` for item processing |
| 3 | `search_loop.py:426` | `isinstance(item, MissingEpisode)` for search dispatch |
| 4 | `search_loop.py:534` | `_run_cutoff_pass`: entire bifurcated code path |
| 5 | `routes/settings.py:110` | `_build_client()`: `if sonarr ... else RadarrClient` |

The `else` clause everywhere implicitly means "radarr". A third type would
silently be treated as Radarr with no error.

### App-specific candidate models

`MissingEpisode` has 7 fields:

```
episode_id, series_id, series_title, episode_title, season, episode, air_date_utc
```

`MissingMovie` has 10 fields:

```
movie_id, title, year, status, minimum_availability, is_available,
in_cinemas, physical_release, release_date, digital_release
```

### App-specific unreleased logic

- **Sonarr**: `_is_within_unreleased_delay(item.air_date_utc, delay_hrs)`
  -- single date check
- **Radarr**: `_radarr_unreleased_reason()` -- 4-layer check:
  1. `_is_within_unreleased_delay` using the release anchor
     (`digital_release > physical_release > release_date > in_cinemas`)
  2. `movie.is_available is False`
  3. Status in `{"tba", "announced"}` AND `is_available` is not `True`
  4. `movie.year > current_year` AND `is_available` is not `True` AND
     status != `"released"`

### Sonarr-specific features stored on all instances

- `sonarr_search_mode` (episode vs season_context) lives on ALL `Instance`
  objects and in the DB schema, but only applies to Sonarr
- Season-context uses synthetic negative IDs:
  `-(series_id * 1000 + season_number)`
- Cutoff pass does NOT support season_context mode (always episode-level)

---

## C. Security-Sensitive Boundaries

### 1. API key lifecycle

API keys are encrypted at rest via Fernet (`crypto.py`), decrypted only
when constructing `Instance` objects via `_row_to_instance()`, and passed
to `httpx.AsyncClient` via `X-Api-Key` header. The plaintext key only
exists in memory on `Instance.api_key` and inside the httpx client headers.

**Preservation rule:** Any refactor must not introduce new code paths where
plaintext keys are logged, serialized, or stored outside this lifecycle.

Places where plaintext API keys appear in current code paths:

| Location | Context |
|---|---|
| `clients/base.py:41` | Set as `X-Api-Key` header on `httpx.AsyncClient` |
| `routes/settings.py:277,294,296,355,371,463,468,485,536` | Handled during form processing (create, update, test-connection, toggle) |
| `engine/search_loop.py:295,299,428,441,535,623,666,754` | Passed to client constructors |
| `Instance.api_key` attribute | On every `Instance` object in memory |

API key NEVER appears in: templates (edit form uses `__UNCHANGED__`
sentinel), log messages, JSON API responses, or exception strings (keys
travel via headers, not URLs).

### 2. Master key management

The master key is loaded once in `app.py` lifespan (line 54), stored on
`app.state.master_key`, and passed explicitly as a `master_key=` kwarg. It
is never imported globally.

**Preservation rule:** A factory pattern or adapter registry must not
require the master key to be accessible as module-level state.

### 3. Instance URL validation (SSRF guard)

`url_validation.py` runs at form-submission time only. It blocks:
- Non-http(s) schemes
- `localhost` literal
- Loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`,
  `fe80::/10`), unspecified (`0.0.0.0`, `::`) addresses
- Hostnames that resolve to blocked IPs via `socket.getaddrinfo()`

Intentionally allowed: RFC-1918 private ranges (Docker/LAN use).

**Known limitations:**
- DNS rebinding / TOCTOU: validation resolves at form time, httpx resolves
  again at connection time
- No validation at request time: once a URL is in the DB, it is used
  without re-validation
- No port restrictions
- No path restrictions (base_url can contain arbitrary path segments)

**Preservation rule:** A new adapter base class or client factory must not
bypass this validation gate. The validation boundary stays at the settings
route.

### 4. Auth / session / CSRF

`AuthMiddleware` is global (Starlette `BaseHTTPMiddleware`). CSRF enforced
on all state-changing requests via `hmac.compare_digest()`. Session cookies:
`HttpOnly=True`, `SameSite=Strict`. Rate limiter: 5 attempts per 60s per
IP.

**No refactoring of the engine or client layer affects this boundary.**

### 5. Logging boundary

No API key, password, or session secret is ever written to logs or
`search_log`. Exception messages from httpx could theoretically contain URL
info, but keys travel via headers. The `search_log.message` column stores
exception strings and is exposed via `/api/logs`.

**Preservation rule:** Any new queue endpoint or adapter method must follow
the same pattern: keys in headers, never in URLs or logged exception
context.

### 6. SQL injection prevention

`update_instance()` uses an explicit `allowed_cols` allowlist
(lines 245-261) for dynamic SQL. Three dynamic SQL locations all use
code-controlled allowlists, never user input.

**Preservation rule:** Any new per-instance settings (e.g., queue-related
flags) must be added to the `allowed_cols` allowlist.

### 7. `__UNCHANGED__` sentinel for API keys

The edit form never sends the real API key to the browser. `value=
"__UNCHANGED__"` is rendered. If submitted as-is during creation, the
connection test would fail.

### 8. Connection-verification gate

Instance creation/update requires a successful connection test. The server
re-verifies independently (`connection_verified == "true"` in form data
AND server-side re-test at lines 352-356, 465-469).

**Preservation rule:** Any new client types must be constructible for
connection testing via the same gate.

---

## D. Architecture Options Considered

### Option 1: Do nothing (status quo)

Keep the binary `if/else` pattern. Add queue-awareness as another branch
inside `run_instance_search()`.

| Pros | Cons |
|---|---|
| No abstraction risk | Cutoff pass already 260 lines duplicated |
| No migration needed | Adding queue checks quadruples integration points |
| Minimal change surface | `search_loop.py` grows toward 1200+ lines |

### Option 2: Extract per-app adapter modules (full adapter pattern)

Create per-app adapter classes that encapsulate: client construction,
candidate parsing, label generation, unreleased logic, search dispatch,
and item-type mapping. The search loop becomes a generic pipeline.

| Pros | Cons |
|---|---|
| Eliminates all duplication | Introduces a new abstraction layer |
| Queue-awareness slots in cleanly | Requires defining an adapter contract |
| Future apps add adapter modules | Could be premature with only two apps |

### Option 3: Extract only the cutoff pass deduplication

Refactor `_run_cutoff_pass` to use the same polymorphic pattern as the
missing pass, without a full adapter abstraction.

| Pros | Cons |
|---|---|
| Smaller change | Type-branching still scattered in search_loop.py |
| Eliminates worst duplication | Queue-awareness still needs multiple branch points |
| No new abstractions | |

### Option 4: Normalized candidate model + thin adapter functions (recommended)

Introduce a small `SearchCandidate` dataclass that normalizes what the
search loop needs. Per-app adapter functions convert
`MissingEpisode`/`MissingMovie` into `SearchCandidate`. Keep app-specific
clients unchanged.

| Pros | Cons |
|---|---|
| Unified pipeline eliminates ~260 lines | Can't fully normalize Radarr's unreleased check |
| Preserves existing client code exactly | New dataclass must stay in sync |
| Adapter is a mapping function, not a class hierarchy | |
| Queue-awareness becomes single insertion point | |
| Doesn't expand trust boundaries | |

---

## E. Recommended Design Direction

**Option 4 (normalized candidate model + thin adapter functions).**

### Why this option

1. **Solves the real problem.** The search loop duplicates ~260 lines in
   the cutoff pass. A `SearchCandidate` lets the engine run a single
   pipeline for both missing and cutoff, for both app types.

2. **Preserves existing clients exactly.** `SonarrClient` and
   `RadarrClient` keep returning `MissingEpisode` and `MissingMovie`.
   The adapter is a pure mapping function at the boundary.

3. **Keeps unreleased logic app-specific.** The adapter function computes
   `unreleased_reason: str | None` during candidate construction. The
   engine just sees "this candidate has an unreleased_reason or not."

4. **Supports queue-awareness naturally.** Queue items can be checked
   against the same `(item_id, item_type)` identity space.

5. **Doesn't expand trust boundaries.** Adapter functions receive
   already-decrypted `Instance` + raw client response data. No new
   secret handling. No new outbound communication.

6. **Doesn't require a class hierarchy.** A dispatch dict
   `{InstanceType: adapter_functions}` is sufficient.

### What this is NOT

This is not a generic "app framework" or plugin system. It is an internal
decomposition of `search_loop.py` that:

- Extracts per-app mapping logic into named, testable functions
- Unifies the search pipeline into one code path
- Makes the cutoff pass a parameter of the pipeline
  (`search_kind="missing"|"cutoff"`) instead of a separate function

### The SearchCandidate model

```python
@dataclass(frozen=True)
class SearchCandidate:
    item_id: int              # episode_id, movie_id, or synthetic season ID
    item_type: ItemType       # "episode" or "movie"
    label: str                # human-readable label for logging
    unreleased_reason: str | None  # None = eligible, str = skip reason
    group_key: tuple[int, int] | None  # (series_id, season) for dedup, or None
    search_payload: dict[str, Any]     # opaque data for dispatch function
```

### The adapter function contract

Per app, three functions:

```python
# Convert raw API response item to normalized candidate
def adapt_missing(item: MissingEpisode, instance: Instance) -> SearchCandidate
def adapt_cutoff(item: MissingEpisode, instance: Instance) -> SearchCandidate

# Dispatch the actual search command
async def dispatch_search(client: SonarrClient, candidate: SearchCandidate) -> None
```

And a client factory:

```python
def make_client(instance: Instance) -> ArrClient
```

These stay in per-app modules (e.g., `engine/adapters/sonarr.py`,
`engine/adapters/radarr.py`).

### The adapter registry

```python
@dataclass(frozen=True)
class AppAdapter:
    adapt_missing: Callable[[Any, Instance], SearchCandidate]
    adapt_cutoff: Callable[[Any, Instance], SearchCandidate]
    dispatch_search: Callable[[ArrClient, SearchCandidate], Awaitable[None]]
    make_client: Callable[[Instance], ArrClient]

ADAPTERS: dict[InstanceType, AppAdapter] = {
    InstanceType.sonarr: AppAdapter(...),
    InstanceType.radarr: AppAdapter(...),
}
```

---

## F. Reusable vs App-Specific

### Should become reusable (engine-level)

| Component | Why | Trust boundary change? |
|---|---|---|
| Search pipeline (page, evaluate, dispatch, record) | Duplicated 3x. One pipeline eliminates ~260 lines. | No |
| `SearchCandidate` dataclass | Normalizes identity so engine doesn't need `isinstance` checks | No |
| Hourly-cap checking | Already generic. No change needed. | No |
| Cooldown checking | Already generic. No change needed. | No |
| `_write_log` | Already generic. No change needed. | No |
| `_is_within_unreleased_delay` | Used by both Sonarr and Radarr adapters | No |

### Should remain app-specific

| Component | Why |
|---|---|
| Client classes (`SonarrClient`, `RadarrClient`) | Different endpoints, response schemas, command payloads. Abstracting would create leaky abstraction. |
| Unreleased-delay logic | Radarr's 4-layer check is fundamentally different from Sonarr's single-date check. |
| Label generation | `"Series - S01E02 - Title"` vs `"Title (Year)"` are format-specific. |
| Season-context grouping | Sonarr-specific. No other app has this exact concept. |
| `sonarr_search_mode` | Only meaningful for Sonarr. Do not rename to something generic. |

### For every proposed abstraction

| Abstraction | Problem it solves | Changes trust boundaries? | Increases attack surface? | Simpler alternative? |
|---|---|---|---|---|
| `SearchCandidate` | Eliminates 260-line cutoff duplication | No | No | Option 3 (partial dedup) is simpler but doesn't support queue-awareness cleanly |
| Adapter dispatch dict | Replaces scattered `if/else` branches | No | No (raises `ValueError` for unknown types) | Keep `if/else` but still duplicated |
| `_run_search_pass()` | Single pipeline for missing+cutoff | No | No | None that solves both passes |

---

## G. Future-App Compatibility Analysis

### Structural groupings

The five *arr apps fall into three adapter families:

| Family | Apps | API version | Hierarchy | Item type |
|---|---|---|---|---|
| Series-based | Sonarr, Whisparr | `/api/v3/` | Series -> Episode | episode |
| Flat-media | Radarr | `/api/v3/` | Movie (self-contained) | movie |
| Parent-item | Lidarr, Readarr | `/api/v1/` | Artist->Album / Author->Book | album / book |

### Endpoint comparison (all five apps)

#### Wanted/Missing

| App | Path | Key fields |
|---|---|---|
| Sonarr | `GET /api/v3/wanted/missing` | `id`, `seriesId`, `title`, `airDateUtc`, `seasonNumber`, `episodeNumber` |
| Radarr | `GET /api/v3/wanted/missing` | `id`, `title`, `year`, `status`, `inCinemas`, `digitalRelease`, `isAvailable` |
| Whisparr | `GET /api/v3/wanted/missing` | `id`, `seriesId`, `title`, `releaseDate` (DateOnly, NOT `airDateUtc`) |
| Lidarr | `GET /api/v1/wanted/missing` | `id`, `title`, `artistId`, `releaseDate` (date-time) |
| Readarr | `GET /api/v1/wanted/missing` | `id`, `title`, `authorId`, `releaseDate` (date-time) |

All return the same paginated envelope: `{page, pageSize, sortKey,
sortDirection, totalRecords, records}`.

#### Search commands

| App | Command name | Payload |
|---|---|---|
| Sonarr | `EpisodeSearch` | `{"name": "EpisodeSearch", "episodeIds": [int]}` |
| Sonarr | `SeasonSearch` | `{"name": "SeasonSearch", "seriesId": int, "seasonNumber": int}` |
| Radarr | `MoviesSearch` | `{"name": "MoviesSearch", "movieIds": [int]}` |
| Whisparr | `EpisodeSearch` | Same as Sonarr |
| Whisparr | `SeasonSearch` | Same as Sonarr |
| Lidarr | `AlbumSearch` | `{"name": "AlbumSearch", "albumIds": [int]}` |
| Readarr | `BookSearch` | `{"name": "BookSearch", "bookIds": [int]}` |

All via `POST /api/{version}/command`. The `CommandResource` schema is
identical across all five apps.

#### Queue endpoints

| App | Path | Item ID field | Parent ID field | Include params |
|---|---|---|---|---|
| Sonarr | `GET /api/v3/queue` | `episodeId` | `seriesId` | `includeSeries`, `includeEpisode` |
| Radarr | `GET /api/v3/queue` | (N/A, movie is item) | `movieId` | `includeMovie` |
| Whisparr | `GET /api/v3/queue` | `episodeId` | `seriesId` | `includeSeries`, `includeEpisode` |
| Lidarr | `GET /api/v1/queue` | `albumId` | `artistId` | `includeArtist`, `includeAlbum` |
| Readarr | `GET /api/v1/queue` | `bookId` | `authorId` | `includeAuthor`, `includeBook` |

Queue/details endpoints also available at `/queue/details` with
per-item filtering.

#### Release date handling

| App | Date field(s) | Type |
|---|---|---|
| Sonarr | `airDate` (local), `airDateUtc` (UTC) | Two fields |
| Radarr | `inCinemas`, `physicalRelease`, `digitalRelease`, `releaseDate` | Four nullable date-times |
| Whisparr | `releaseDate` | DateOnly (NOT date-time) |
| Lidarr | `releaseDate` | date-time, nullable |
| Readarr | `releaseDate` | date-time, nullable |

### Whisparr vs Sonarr (fork analysis)

Whisparr is a Sonarr fork. Shared: endpoint paths, command names, queue
structure, `seriesId` + `episodeId` identification.

**Key divergences from Sonarr:**
1. `releaseDate` (DateOnly) replaces `airDate`/`airDateUtc`
2. No `episodeNumber` on `EpisodeResource`
3. No scene numbering fields
4. Adds `actors`, `seriesTitle` (direct field), `grabbed` boolean
5. Queue `status` is `string` (nullable) instead of `QueueStatus` enum
6. Queue `sizeleft`/`timeleft` are NOT deprecated

**Verdict:** A Sonarr adapter could support Whisparr with conditional
date-field mapping. Not a "just works" scenario.

### Lidarr vs Readarr

Nearly identical structure. Different entity names (artist/album vs
author/book) but same patterns. An adapter for one is trivially adaptable
to the other.

### Would the proposed design help with future apps?

**Yes, without optimization for them.** The `SearchCandidate` + adapter
pattern means adding a new app = write new client + adapter functions +
add to `InstanceType` enum + DB migration. The engine pipeline doesn't
change.

**No code should be written now to accommodate future apps.** The DB CHECK
constraints, `InstanceType` enum, and `ItemType` literal should remain
locked to current values.

### Structural barriers to a fully unified adapter

| Barrier | Details |
|---|---|
| API version prefix | `/api/v3/` for Sonarr/Radarr/Whisparr, `/api/v1/` for Lidarr/Readarr |
| Item type terminology | episode, movie, album, book -- all different |
| Search command names | `EpisodeSearch`, `MoviesSearch`, `AlbumSearch`, `BookSearch` |
| Date field names | `airDateUtc` vs `inCinemas`+3 more vs `releaseDate` (DateOnly) vs `releaseDate` (date-time) |
| Parent/child hierarchy | Two-level (Sonarr/Whisparr/Lidarr/Readarr) vs flat (Radarr) |
| Queue filter params | `seriesIds` vs `movieIds` vs `artistIds` -- Whisparr/Readarr have none |
| Availability logic | Only Radarr has `minimumAvailability` + `isAvailable` |
| Season-context search | Only Sonarr/Whisparr have `SeasonSearch` |

---

## H. Compatibility and Non-Breaking Analysis

| Question | Answer |
|---|---|
| DB schema changes needed now? | **No.** Refactor is purely code reorganization. |
| Queue-awareness needs schema change? | Only if adding `skip_if_queued` setting. Simple `ALTER TABLE ADD COLUMN`. |
| Current instance types can remain? | **Yes.** `sonarr`/`radarr` unchanged. |
| Log item types can remain? | **Yes.** `episode`/`movie` unchanged. |
| Settings/UI can remain? | **Yes.** No form changes, no template changes. |
| API/status payloads can remain? | **Yes.** Same `search_log` data, same status computation. |

---

## I. Recommended Sequencing

### Phase 1: Internal refactor (PR 1 -- no behavior change)

Extract `SearchCandidate`, adapter functions, unified `_run_search_pass()`.
Eliminate cutoff-pass duplication. All existing tests must pass with
identical behavior.

### Phase 2: Queue-awareness (PR 2 -- new behavior)

Add queue-fetching to clients, queue-gating to pipeline, optional
`skip_if_queued` instance setting.

### Why this order

| Approach | Queue integration points | Risk |
|---|---|---|
| Queue first (no refactor) | 4 locations (missing-sonarr, missing-radarr, cutoff-sonarr, cutoff-radarr) | Quadrupled integration surface; eventual refactor is harder |
| Refactor first | 1 location (`_run_search_pass` pipeline) | Clean insertion point |
| Combined | 1 location but larger diff | Harder to verify zero behavior drift from refactor |

**Minimum enabling change before queue-awareness:** The cutoff-pass
deduplication. Without it, queue-awareness must be implemented 4 times.

---

## J. API-Doc and Workflow Recommendation

- All five OpenAPI specs are vendored locally in `docs/api/`:
  `sonarr_openapi.json`, `radarr_openapi.json`, `whisparr_openapi.json`,
  `lidarr_openapi.json`, `readarr_openapi.json`
- Reference these when implementing queue endpoint client methods or
  future app adapters
- Queue endpoint schemas are fully documented in the local specs
- Follow `docs/api-context.md` guidelines for new client methods
- The `api-snapshot-refresh.yml` workflow auto-refreshes all five specs
  weekly and syncs hashes in `tests/test_docs_api.py`

---

## K. Step-by-Step Implementation Plan

### Phase 1: Internal refactor (no behavior change)

#### Step 1.1: Define `SearchCandidate` dataclass

- New file: `src/houndarr/engine/candidates.py`
- Frozen dataclass with fields:
  - `item_id: int`
  - `item_type: ItemType`
  - `label: str`
  - `unreleased_reason: str | None`
  - `group_key: tuple[int, int] | None` (for season-context dedup)
  - `search_payload: dict[str, Any]` (opaque data for dispatch function)
- `ItemType` stays as `Literal["episode", "movie"]`
- Move `_is_within_unreleased_delay` here (used by both adapters)

#### Step 1.2: Write Sonarr adapter functions

- New file: `src/houndarr/engine/adapters/sonarr.py`
- `adapt_missing(item: MissingEpisode, instance: Instance) ->
  SearchCandidate` -- encapsulates episode-mode vs season-context
  branching, label generation, unreleased check, synthetic season ID
- `adapt_cutoff(item: MissingEpisode, instance: Instance) ->
  SearchCandidate` -- same but always episode-mode (matching current
  cutoff behavior)
- `async def dispatch_search(client: SonarrClient, candidate:
  SearchCandidate) -> None` -- reads `search_payload` to determine
  `EpisodeSearch` vs `SeasonSearch`
- Move `_episode_label`, `_season_context_label`, `_season_item_id` here

#### Step 1.3: Write Radarr adapter functions

- New file: `src/houndarr/engine/adapters/radarr.py`
- `adapt_missing(item: MissingMovie, instance: Instance) ->
  SearchCandidate` -- encapsulates `_radarr_unreleased_reason`, label
  generation
- `adapt_cutoff(item: MissingMovie, instance: Instance) ->
  SearchCandidate` -- same
- `async def dispatch_search(client: RadarrClient, candidate:
  SearchCandidate) -> None` -- sends `MoviesSearch` command
- Move `_radarr_release_anchor`, `_radarr_unreleased_reason`,
  `_movie_label`, `_RADARR_UNRELEASED_STATUSES` here

#### Step 1.4: Create adapter registry

In `engine/adapters/__init__.py`:

```python
@dataclass(frozen=True)
class AppAdapter:
    adapt_missing: Callable
    adapt_cutoff: Callable
    dispatch_search: Callable
    make_client: Callable

ADAPTERS: dict[InstanceType, AppAdapter] = {
    InstanceType.sonarr: AppAdapter(
        adapt_missing=sonarr.adapt_missing,
        adapt_cutoff=sonarr.adapt_cutoff,
        dispatch_search=sonarr.dispatch_search,
        make_client=sonarr.make_client,
    ),
    InstanceType.radarr: AppAdapter(
        adapt_missing=radarr.adapt_missing,
        adapt_cutoff=radarr.adapt_cutoff,
        dispatch_search=radarr.dispatch_search,
        make_client=radarr.make_client,
    ),
}
```

#### Step 1.5: Unify the search pipeline

Extract `_run_search_pass()` that takes:
- `instance`, `client`, `adapt_fn`, `dispatch_fn`
- `search_kind`, `batch_size`, `hourly_cap`, `cooldown_days`
- `page_size_fn`, `scan_budget_fn`
- `cycle_id`, `cycle_trigger`

`run_instance_search()` becomes:

```python
adapter = ADAPTERS[instance.type]
client = adapter.make_client(instance)

searched = 0
if instance.batch_size > 0:
    searched += await _run_search_pass(
        instance, client, adapter.adapt_missing, adapter.dispatch_search,
        search_kind="missing", batch_size=instance.batch_size,
        hourly_cap=instance.hourly_cap, cooldown_days=instance.cooldown_days,
        page_size_fn=_missing_page_size, scan_budget_fn=_missing_scan_budget,
        cycle_id=cycle_id, cycle_trigger=cycle_trigger,
    )

if instance.cutoff_enabled and instance.cutoff_batch_size > 0:
    searched += await _run_search_pass(
        instance, client, adapter.adapt_cutoff, adapter.dispatch_search,
        search_kind="cutoff", batch_size=instance.cutoff_batch_size,
        hourly_cap=instance.cutoff_hourly_cap, cooldown_days=instance.cutoff_cooldown_days,
        page_size_fn=_cutoff_page_size, scan_budget_fn=_cutoff_scan_budget,
        cycle_id=cycle_id, cycle_trigger=cycle_trigger,
    )

return searched
```

Delete `_run_cutoff_pass()` entirely.

#### Step 1.6: Move helper functions

| Function | From | To |
|---|---|---|
| `_radarr_release_anchor` | `search_loop.py` | `engine/adapters/radarr.py` |
| `_radarr_unreleased_reason` | `search_loop.py` | `engine/adapters/radarr.py` |
| `_RADARR_UNRELEASED_STATUSES` | `search_loop.py` | `engine/adapters/radarr.py` |
| `_episode_label` | `search_loop.py` | `engine/adapters/sonarr.py` |
| `_season_context_label` | `search_loop.py` | `engine/adapters/sonarr.py` |
| `_season_item_id` | `search_loop.py` | `engine/adapters/sonarr.py` |
| `_movie_label` | `search_loop.py` | `engine/adapters/radarr.py` |
| `_is_within_unreleased_delay` | `search_loop.py` | `engine/candidates.py` |
| `_parse_iso_utc` | `search_loop.py` | `engine/candidates.py` |
| Page-size/budget helpers | `search_loop.py` | Stay in `search_loop.py` |

#### Step 1.7: Update tests

- Existing tests should pass with minimal changes (import path updates)
- Add unit tests for adapter functions in isolation
- Add a "golden test" that captures exact `search_log` row sequence for a
  known input, verifying identical output before and after refactor
- The `respx`-based integration tests continue to work because actual HTTP
  calls are unchanged

#### Step 1.8: Run all quality gates

`ruff check`, `ruff format`, `mypy`, `bandit`, `pytest` -- all must pass.

### Phase 2: Queue-awareness (new behavior)

#### Step 2.1: Add `get_queue_item_ids()` to clients

- `SonarrClient.get_queue_item_ids() -> set[int]` -- calls
  `GET /api/v3/queue` with `includeSeries=false`, `includeEpisode=false`,
  `pageSize=200`, extracts `episodeId` from each record
- `RadarrClient.get_queue_item_ids() -> set[int]` -- calls
  `GET /api/v3/queue` with `includeMovie=false`, `pageSize=200`, extracts
  `movieId`
- Add to `ArrClient` ABC:
  `@abstractmethod async def get_queue_item_ids(self) -> set[int]`

#### Step 2.2: Add queue-gating to the unified pipeline

At the start of `_run_search_pass()`, call
`client.get_queue_item_ids()` once.

In the eligibility pipeline, after cooldown check and before search
dispatch: if `candidate.item_id in queued_ids`, log `"skipped"` with
reason `"already in download queue"`, continue.

**Season-context limitation:** The queue returns `episodeId`, but
season-context uses synthetic negative IDs. The simplest correct
approach: skip the queue check for season-context candidates (where
`candidate.group_key is not None`). Document this as a known limitation.

Eligibility pipeline order:

1. Dedup (seen_item_ids)
2. Unreleased delay
3. Hourly cap
4. Cooldown
5. **Queue check** (new)
6. Search dispatch

#### Step 2.3: Add optional `skip_if_queued` instance setting

- Default: `True` (queue-aware by default)
- DB migration v5:
  `ALTER TABLE instances ADD COLUMN skip_if_queued INTEGER NOT NULL DEFAULT 1`
- Add to `Instance` dataclass
- Add to `create_instance`, `update_instance` `allowed_cols`
- Add to settings form
- `_run_search_pass` checks `instance.skip_if_queued` before calling
  `get_queue_item_ids()`

#### Step 2.4: Handle queue fetch failures gracefully

If `get_queue_item_ids()` raises `httpx.HTTPError`:
- Log an `"info"` row: `"queue check unavailable, proceeding without"`
- Set `queued_ids = set()` (empty, so no items are skipped)
- Continue the search pass normally

This ensures queue-awareness is advisory, not blocking.

#### Step 2.5: Update tests

- Mock `GET /api/v3/queue` responses in `respx`
- Test: item in queue -> skipped with reason `"already in download queue"`
- Test: item not in queue -> searched normally
- Test: queue fetch failure -> info log, continue searching
- Test: `skip_if_queued=False` -> queue not fetched
- Test: season-context candidates -> queue check skipped
- Test: paginated queue (>200 items) -> all pages fetched

#### Step 2.6: Update settings form

- Add toggle for `skip_if_queued` in instance form
- Default checked (True)
- Applicable to both Sonarr and Radarr

#### Step 2.7: Run all quality gates

---

## L. Risks and Tradeoffs

### 1. Subtle behavior drift during refactor

**Risk:** The unified pipeline might process items in a slightly different
order or handle edge cases differently.
**Mitigation:** Write a "golden test" that captures exact `search_log` row
sequence for a known input scenario, run against both old and new code,
assert identical output.

### 2. Cooldown/logging identity mismatches

**Risk:** If the adapter computes `item_id` differently, cooldowns won't
match historical data.
**Mitigation:** Adapter functions must produce the exact same `item_id`
values. For season-context: same `_season_item_id` formula. Test
explicitly.

### 3. Overabstracting too early

**Risk:** `SearchCandidate` could grow to accommodate hypothetical future
apps, adding unused fields.
**Mitigation:** Define with exactly the fields the current engine needs.
No "reserved for future use" fields.

### 4. Building "fake genericity"

**Risk:** The adapter dispatch dict is generic, but with only two entries
it's arguably a more complex `if/else`.
**Mitigation:** The real value is the unified `_run_search_pass()` that
eliminates 260 lines. The dispatch dict is the mechanism, not the goal.

### 5. Making current code harder to reason about

**Risk:** Introducing indirection (candidate -> adapter -> dispatch) adds
abstraction.
**Mitigation:** Each adapter function is small and testable in isolation.
The search pipeline becomes shorter and more linear. Net readability
improves.

### 6. Security regressions from abstraction changes

**Risk:** Adapter module accidentally logs an API key, or dispatch dict
allows unregistered type.
**Mitigation:** Adapter functions receive `Instance` objects (already
decrypted) and client objects (already constructed). They don't handle
keys. Dispatch dict raises `ValueError` for unknown types. `bandit`
continues to run.

### 7. Accidental trust-boundary expansion

**Risk:** Adapter contract grows to include queue fetching, and a future
adapter fetches from unexpected endpoint.
**Mitigation:** Queue fetching stays in well-typed client classes. The
adapter never makes HTTP calls -- it only transforms data.

### 8. Queue-awareness false negatives

**Risk:** Race condition between queue fetch and search dispatch.
**Mitigation:** Inherently best-effort. Skipped items retry on next cycle.
Document explicitly. Queue check reduces unnecessary searches but doesn't
guarantee zero duplicates.

### 9. Queue-awareness performance

**Risk:** Extra API call per search pass could slow down cycles.
**Mitigation:** One call per pass (not per item). `pageSize=200` covers
most queues in one request. Timeout is 30s (same as other API calls).
Failure is gracefully handled.

---

## M. Queue-Awareness Bridge Plan

### Where queue fetching belongs

In the **client classes** (`SonarrClient.get_queue_item_ids()`,
`RadarrClient.get_queue_item_ids()`), as a new method on the `ArrClient`
ABC. The client knows the correct endpoint and field names, already holds
the HTTP connection and auth headers, and no new trust boundary is created.

### Where queue normalization belongs

Nowhere for skip-if-queued. The queue response is reduced to `set[int]`
at the client level. The engine receives a plain set and checks
membership. No queue model or dataclass is needed.

If Houndarr ever needs to display queue information in the UI, a
`QueueSummary` dataclass could be added later as a separate feature.

### Where queue gating belongs

In `_run_search_pass()`, as a check in the eligibility pipeline, after
cooldown and before search dispatch.

### How often should queue be fetched

**Once per search pass.** Missing pass fetches once, cutoff pass fetches
once. This is 1-2 API calls per cycle (or 0 if `skip_if_queued=False`).

### How to avoid duplication

With the unified `_run_search_pass()`, queue-gating is implemented once.
Client methods for fetching queue IDs are per-app, but the gating logic
is shared.

### How to preserve security posture

- Queue fetch uses the same `httpx.AsyncClient` and `X-Api-Key` header
- Queue data reduced to `set[int]` -- no sensitive info persisted or logged
- Skip reason logged is `"already in download queue"` -- no queue details
- Queue fetch failure -> graceful degradation, not blocking

---

## N. Final Recommendation

**The refactor is warranted.** The cutoff-pass duplication (260 lines) is
a concrete maintainability problem today. Queue-awareness would make it
worse. The `SearchCandidate` + adapter function pattern is the smallest
change that solves both problems.

**Do the refactor first (Phase 1), then queue-awareness (Phase 2).** Two
PRs, each independently testable. Phase 1 changes zero behavior. Phase 2
adds one new behavior.

**Do not pre-build for Whisparr, Lidarr, or Readarr.** The design makes
adding them easier later, but no code should target them now.

### Minimum change to actually do

1. Define `SearchCandidate` in `engine/candidates.py`
2. Write adapter functions for Sonarr and Radarr
3. Extract unified `_run_search_pass()` in `search_loop.py`
4. Eliminate duplicated `_run_cutoff_pass()`
5. Move app-specific helpers into adapter modules
6. Update imports and tests
7. Verify all quality gates pass with zero behavior change

### What to deliberately avoid

- Do not add new instance types, item types, or DB CHECK values
- Do not write Whisparr/Lidarr/Readarr clients
- Do not create an abstract `Adapter` base class
- Do not add queue-awareness in the same PR as the refactor
- Do not add "plugin" or "registration" mechanisms
- Do not rename `sonarr_search_mode` to something generic
- Do not modify the database schema in Phase 1
- Do not change any API response format, status payload, or log structure
- Do not change UI, templates, or settings forms in Phase 1
- Do not relax any CHECK constraints or validation rules
- Do not move the SSRF validation boundary
- Do not introduce module-level master key state or global client registries

---

## Appendix 1: Complete Search Flow Map

### Startup sequence

```
Supervisor.__init__(master_key)
  |
  v
Supervisor.start()
  |
  +-> list_instances(master_key)  [services/instances.py]
  |     SELECT * FROM instances -> _row_to_instance() (decrypts API keys)
  |
  +-> For each enabled instance:
  |     start_instance_task(id, instance=instance)
  |       asyncio.create_task(_instance_loop(id))
  |
  +-> _write_log(action="info", message="Supervisor started N task(s)")
```

### Instance loop (per instance, runs forever)

```
_instance_loop(instance_id)
  |
  +-> asyncio.sleep(10)  [startup grace]
  |
  +-> LOOP:
        |
        +-> get_instance(id, master_key)  [re-fetch for current settings]
        +-> if not exists or not enabled: return
        |
        +-> _run_search_cycle(instance, cycle_trigger="scheduled")
        |     Acquire per-instance asyncio.Lock
        |     Generate cycle_id = uuid4()
        |     run_instance_search(instance, master_key, cycle_id, cycle_trigger)
        |     Catch httpx.TransportError -> return True (connection error)
        |     Catch Exception -> _write_log(action="error"), return False
        |
        +-> If connection error:
        |     First failure: _write_log(action="error"), enter retry state
        |     asyncio.sleep(30)
        +-> Else:
              If recovering: _write_log(action="info")
              asyncio.sleep(instance.sleep_interval_mins * 60)
```

### Search cycle (run_instance_search)

```
run_instance_search(instance, master_key, cycle_id, cycle_trigger)
  |
  +-- PHASE 1: Client construction (lines 293-300)
  |     if sonarr: SonarrClient, item_type="episode"
  |     else:      RadarrClient,  item_type="movie"
  |
  +-- PHASE 2: Missing pass (if batch_size > 0)
  |     |
  |     +-- _count_searches_last_hour(instance.id, "missing")
  |     |
  |     +-- FOR page in 1..3:
  |           Break if searched >= batch_size or scanned >= scan_budget
  |           |
  |           +-- client.get_missing(page, page_size)
  |           |     Sonarr: GET /api/v3/wanted/missing -> list[MissingEpisode]
  |           |     Radarr: GET /api/v3/wanted/missing -> list[MissingMovie]
  |           |
  |           +-- FOR item in items:
  |                 |
  |                 +-- TYPE DISPATCH (isinstance check):
  |                 |     MissingEpisode:
  |                 |       episode-mode vs season-context
  |                 |       unreleased: _is_within_unreleased_delay(air_date_utc)
  |                 |     MissingMovie:
  |                 |       unreleased: _radarr_unreleased_reason() [4 layers]
  |                 |
  |                 +-- DEDUP: seen_item_ids set
  |                 +-- CHECK: unreleased delay -> skip + log
  |                 +-- CHECK: hourly cap -> skip + log + stop_pass
  |                 +-- CHECK: cooldown -> skip + log
  |                 |
  |                 +-- SEARCH DISPATCH:
  |                 |     Episode + season_context: SonarrClient.search_season()
  |                 |     Episode + episode_mode:   SonarrClient.search()
  |                 |     Movie:                    RadarrClient.search()
  |                 |
  |                 +-- POST-SEARCH:
  |                       record_search() [upsert cooldowns]
  |                       _write_log(action="searched")
  |
  +-- PHASE 3: Cutoff pass (if cutoff_enabled)
        _run_cutoff_pass(instance, cycle_id, cycle_trigger)
          Same structure but:
          - Uses cutoff_batch_size, cutoff_hourly_cap, cutoff_cooldown_days
          - search_kind="cutoff"
          - Sonarr: always episode-mode (no season_context)
          - Fully bifurcated: ~130 lines sonarr, ~130 lines radarr
```

### Data models flow

```
DB (instances table)
  -> _row_to_instance() + decrypt()
    -> Instance dataclass
      -> Passed to run_instance_search()
        -> Creates SonarrClient or RadarrClient
          -> client.get_missing() returns list[MissingEpisode|MissingMovie]
            -> Eligibility pipeline (unreleased, hourly cap, cooldown)
              -> client.search() / search_season()  -> POST /api/v3/command
              -> record_search()  -> UPSERT cooldowns table
              -> _write_log()  -> INSERT search_log table
```

---

## Appendix 2: Complete sonarr/radarr Reference Inventory

Every location in the codebase that references sonarr or radarr, organized
by what would need to change to add a new instance type.

### Must change (will break or produce wrong behavior)

| # | File | Lines | What |
|---|---|---|---|
| 1 | `services/instances.py` | 28-32 | `InstanceType` enum: add new value |
| 2 | `database.py` | 30 | `CHECK(type IN (...))`: add new value (requires table rebuild migration) |
| 3 | `clients/` | -- | Add new client module (e.g., `lidarr.py`) |
| 4 | `routes/settings.py` | 110-113 | `_build_client()`: handle new type |
| 5 | `routes/settings.py` | 358-364, 471-477 | sonarr_search_mode branching |
| 6 | `engine/search_loop.py` | 293-299, 330, 426, 534 | All `if/else` branches |
| 7 | `templates/partials/instance_form.html` | 55-56 | Type `<select>` dropdown |
| 8 | `templates/partials/instance_row.html` | 6-14 | Type badge (else = radarr) |
| 9 | `templates/partials/pages/dashboard_content.html` | 312-314 | JS type badge (else = radarr) |

### Should change (UX / copy improvements)

| # | File | Lines | What |
|---|---|---|---|
| 10 | `instance_form.html` | 42-45, 63-66 | Placeholder data attributes |
| 11 | `settings_content.html` | 579-585 | `syncAddInstancePlaceholders()` |
| 12 | `settings_content.html` | 389, 550 | Modal subtitle text |
| 13 | `dashboard_content.html` | 239 | Empty-state text |
| 14 | `routes/settings.py` | 268 | Error message "Must be Sonarr or Radarr" |
| 15 | `base.html` | 6 | Meta description |
| 16 | `app.py` | 73, 99 | Descriptive text |
| 17 | `settings_help_content.html` | 102, 108, 112 | Help text |
| 18 | `config.py` | 96 | Type-specific defaults |
| 19 | `cooldowns.item_type` CHECK | database.py | Add new item type value |
| 20 | `search_log.item_type` CHECK | database.py | Same |

### Service / model layer references

| File | Lines | What |
|---|---|---|
| `services/instances.py:35-39` | `SonarrSearchMode` enum | Sonarr-specific |
| `services/instances.py:67` | `Instance.sonarr_search_mode` field | On all instances, only used by Sonarr |
| `services/instances.py:95,121,141,155,173,239,260,273-274` | sonarr_search_mode handling | |
| `services/cooldown.py:42,82` | Docstrings: "Sonarr episode ID or Radarr movie ID" | |
| `services/url_validation.py:5,95,116` | Docstrings: "Sonarr and Radarr", "http://sonarr:8989" | |

### Engine references

| File | Lines | What |
|---|---|---|
| `search_loop.py:16-17` | Imports `RadarrClient`, `SonarrClient` |
| `search_loop.py:23` | Imports `InstanceType`, `SonarrSearchMode` |
| `search_loop.py:40` | `_RADARR_UNRELEASED_STATUSES` |
| `search_loop.py:126-142` | `_radarr_release_anchor()`, `_radarr_unreleased_reason()` |
| `search_loop.py:155-196` | Label builders (episode, season, movie) |
| `search_loop.py:177-185` | Season-context synthetic ID logic |
| `search_loop.py:265,293-299` | Client construction branch |
| `search_loop.py:331` | `sonarr_search_mode` check |
| `search_loop.py:367` | `_radarr_unreleased_reason()` call |
| `search_loop.py:427-444` | Search dispatch branch |
| `search_loop.py:513` | `item_type` assignment |
| `search_loop.py:534-754` | Entire cutoff pass bifurcation |

### Template references

| File | Lines | What |
|---|---|---|
| `instance_form.html:42-45` | Placeholder data attributes |
| `instance_form.html:55-56` | Type dropdown options |
| `instance_form.html:63-66` | URL placeholder attributes |
| `instance_form.html:122-142` | `data-sonarr-only="true"` section |
| `instance_row.html:6-14` | Type badge if/else |
| `dashboard_content.html:239` | Empty-state text |
| `dashboard_content.html:312-314` | JS type badge |
| `settings_content.html:389,550` | Modal subtitle |
| `settings_content.html:579-585` | Placeholder sync function |
| `settings_content.html:588-601` | Sonarr-only controls visibility |
| `settings_content.html:768,861` | Sonarr-only sync calls |
| `settings_help_content.html:102,108,112` | Sonarr search mode help |
| `base.html:6` | Meta description |

---

## Appendix 3: Upstream API Compatibility Matrix

**All five specs are vendored locally in `docs/api/` and auto-refreshed
weekly by `api-snapshot-refresh.yml`:**

| App | Local path | Upstream source |
|---|---|---|
| Sonarr | `docs/api/sonarr_openapi.json` | <https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json> |
| Radarr | `docs/api/radarr_openapi.json` | <https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json> |
| Whisparr | `docs/api/whisparr_openapi.json` | <https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/Whisparr.Api.V3/openapi.json> |
| Lidarr | `docs/api/lidarr_openapi.json` | <https://raw.githubusercontent.com/lidarr/Lidarr/develop/src/Lidarr.Api.V1/openapi.json> |
| Readarr | `docs/api/readarr_openapi.json` | <https://raw.githubusercontent.com/Readarr/Readarr/develop/src/Readarr.Api.V1/openapi.json> |

> Upstream URLs point to the `develop` branch and may change. The local
> vendored copies are the source of truth; `tests/test_docs_api.py`
> verifies their integrity via SHA-256 hashes.

### Wanted/Missing endpoints

| App | Path | Include param | Sort field | Key ID field |
|---|---|---|---|---|
| Sonarr | `GET /api/v3/wanted/missing` | `includeSeries=true` | `airDateUtc` asc | `id` (episodeId) |
| Radarr | `GET /api/v3/wanted/missing` | (none) | `inCinemas` asc | `id` (movieId) |
| Whisparr | `GET /api/v3/wanted/missing` | `includeSeries=true` | `releaseDate` | `id` (episodeId) |
| Lidarr | `GET /api/v1/wanted/missing` | `includeArtist=true` | `releaseDate` | `id` (albumId) |
| Readarr | `GET /api/v1/wanted/missing` | `includeAuthor=true` | `releaseDate` | `id` (bookId) |

### Wanted/Cutoff endpoints

Same structure as missing per-app. Sonarr/Whisparr add
`includeEpisodeFile` param.

### Search commands

| App | Command | Payload |
|---|---|---|
| Sonarr | `EpisodeSearch` | `{"name": "EpisodeSearch", "episodeIds": [int]}` |
| Sonarr | `SeasonSearch` | `{"name": "SeasonSearch", "seriesId": int, "seasonNumber": int}` |
| Radarr | `MoviesSearch` | `{"name": "MoviesSearch", "movieIds": [int]}` |
| Whisparr | `EpisodeSearch` | Same as Sonarr |
| Whisparr | `SeasonSearch` | Same as Sonarr |
| Lidarr | `AlbumSearch` | `{"name": "AlbumSearch", "albumIds": [int]}` |
| Readarr | `BookSearch` | `{"name": "BookSearch", "bookIds": [int]}` |

### Queue endpoints

| App | Path | Item ID field | Parent ID field |
|---|---|---|---|
| Sonarr | `GET /api/v3/queue` | `episodeId` | `seriesId` |
| Radarr | `GET /api/v3/queue` | `movieId` | (N/A) |
| Whisparr | `GET /api/v3/queue` | `episodeId` | `seriesId` |
| Lidarr | `GET /api/v1/queue` | `albumId` | `artistId` |
| Readarr | `GET /api/v1/queue` | `bookId` | `authorId` |

### Queue resource fields (common across all apps)

```
id, title, size, sizeleft, estimatedCompletionTime, status,
trackedDownloadStatus, trackedDownloadState, statusMessages,
errorMessage, downloadId, protocol, downloadClient, indexer,
outputPath, quality, customFormats, customFormatScore,
downloadClientHasPostImportCategory
```

### Queue resource fields (app-specific)

| Field | Sonarr | Radarr | Whisparr | Lidarr | Readarr |
|---|---|---|---|---|---|
| `seriesId` | yes | no | yes | no | no |
| `episodeId` | yes | no | yes | no | no |
| `seasonNumber` | yes | no | yes | no | no |
| `movieId` | no | yes | no | no | no |
| `artistId` | no | no | no | yes | no |
| `albumId` | no | no | no | yes | no |
| `authorId` | no | no | no | no | yes |
| `bookId` | no | no | no | no | yes |
| `languages` | yes | yes | yes | no | no |
| `added` | yes | yes | no | yes | no |
| `downloadForced` | no | no | no | yes | yes |

### Release date fields

| App | Fields | Types |
|---|---|---|
| Sonarr | `airDate`, `airDateUtc` | string (local), date-time (UTC) |
| Radarr | `inCinemas`, `physicalRelease`, `digitalRelease`, `releaseDate` | All nullable date-time |
| Whisparr | `releaseDate` | DateOnly (NOT date-time) |
| Lidarr | `releaseDate` | date-time, nullable |
| Readarr | `releaseDate` | date-time, nullable |

### Item identification for cooldown/logging

| App | Wanted item ID | Queue item ID | Search payload key | Proposed `item_type` |
|---|---|---|---|---|
| Sonarr | `id` (episode) | `episodeId` | `episodeIds` | `"episode"` |
| Radarr | `id` (movie) | `movieId` | `movieIds` | `"movie"` |
| Whisparr | `id` (episode) | `episodeId` | `episodeIds` | `"episode"` |
| Lidarr | `id` (album) | `albumId` | `albumIds` | `"album"` |
| Readarr | `id` (book) | `bookId` | `bookIds` | `"book"` |

### Fork/lineage summary

- **Whisparr = Sonarr fork** with date field divergence (`releaseDate`
  DateOnly vs `airDateUtc`), no `episodeNumber`, additional fields
  (`actors`, `seriesTitle`, `grabbed`)
- **Lidarr and Readarr** are structurally near-identical (same hierarchy
  pattern, same API version, same endpoint patterns, entity name
  substitution)
- **Radarr** is structurally unique (flat hierarchy, multi-date
  availability model)

---

## Appendix 4: Database Schema Reference

### Current DDL (schema version 4)

```sql
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    type                 TEXT    NOT NULL CHECK(type IN ('sonarr', 'radarr')),
    url                  TEXT    NOT NULL,
    encrypted_api_key    TEXT    NOT NULL DEFAULT '',
    batch_size           INTEGER NOT NULL DEFAULT 2,
    sleep_interval_mins  INTEGER NOT NULL DEFAULT 30,
    hourly_cap           INTEGER NOT NULL DEFAULT 4,
    cooldown_days        INTEGER NOT NULL DEFAULT 14,
    unreleased_delay_hrs INTEGER NOT NULL DEFAULT 36,
    cutoff_enabled       INTEGER NOT NULL DEFAULT 0,
    cutoff_batch_size    INTEGER NOT NULL DEFAULT 1,
    cutoff_cooldown_days INTEGER NOT NULL DEFAULT 21,
    cutoff_hourly_cap    INTEGER NOT NULL DEFAULT 1,
    sonarr_search_mode   TEXT    NOT NULL DEFAULT 'episode'
                                CHECK(sonarr_search_mode IN ('episode', 'season_context')),
    enabled              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at           TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS cooldowns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL,
    item_type   TEXT    NOT NULL CHECK(item_type IN ('episode', 'movie')),
    searched_at TEXT    NOT NULL,
    UNIQUE(instance_id, item_id, item_type)
);

CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup
    ON cooldowns(instance_id, item_type, searched_at);

CREATE TABLE IF NOT EXISTS search_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id   INTEGER REFERENCES instances(id) ON DELETE SET NULL,
    item_id       INTEGER,
    item_type     TEXT    CHECK(item_type IN ('episode', 'movie')),
    search_kind   TEXT,
    cycle_id      TEXT,
    cycle_trigger TEXT    CHECK(cycle_trigger IN ('scheduled', 'run_now', 'system')),
    item_label    TEXT,
    action        TEXT    NOT NULL
                         CHECK(action IN ('searched', 'skipped', 'error', 'info')),
    reason        TEXT,
    message       TEXT,
    timestamp     TEXT    NOT NULL
                         DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_search_log_timestamp
    ON search_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_search_log_instance
    ON search_log(instance_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_search_log_cycle
    ON search_log(cycle_id, timestamp DESC);
```

### All CHECK constraints

| Table | Column | Values |
|---|---|---|
| `instances` | `type` | `'sonarr'`, `'radarr'` |
| `instances` | `sonarr_search_mode` | `'episode'`, `'season_context'` |
| `cooldowns` | `item_type` | `'episode'`, `'movie'` |
| `search_log` | `item_type` | `'episode'`, `'movie'` |
| `search_log` | `cycle_trigger` | `'scheduled'`, `'run_now'`, `'system'` |
| `search_log` | `action` | `'searched'`, `'skipped'`, `'error'`, `'info'` |

### Migration history

| Version | What it adds |
|---|---|
| v2 | `search_log.search_kind`, `search_log.item_label`, `instances.cutoff_cooldown_days`, `instances.cutoff_hourly_cap` |
| v3 | `search_log.cycle_id`, `search_log.cycle_trigger` |
| v4 | `instances.sonarr_search_mode` |

### What a v5 migration for new instance type would require

SQLite cannot `ALTER TABLE ... ALTER CONSTRAINT`. Modifying CHECK
constraints requires the table-rebuild pattern:

1. Create new table with updated CHECK
2. Copy all data
3. Drop old table
4. Rename new table
5. Recreate indexes and FK references
6. Do this for all three tables (`instances`, `cooldowns`, `search_log`)

### What a v5 migration for queue-awareness would require

**Option A (skip-if-queued, no persistence):** Simple column addition:
```sql
ALTER TABLE instances ADD COLUMN skip_if_queued INTEGER NOT NULL DEFAULT 1
```

**Option B (persisted queue state):** New table:
```sql
CREATE TABLE IF NOT EXISTS queue_items (
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL,
    item_type   TEXT    NOT NULL CHECK(item_type IN ('episode', 'movie')),
    last_seen   TEXT    NOT NULL,
    UNIQUE(instance_id, item_id, item_type)
);
```

Option A is recommended. Queue state is ephemeral and should be fetched
fresh each cycle.

---

## Appendix 5: Test Coupling Analysis

### Fixture dependency graph

```
tmp_data_dir                  (tempfile.TemporaryDirectory)
  |
  +-- db                      (set_db_path + init_db)
  |     |
  |     +-- seeded_instances  (local fixture, seeds 2 FK rows)
  |           test_search_loop.py: (1, "sonarr"), (2, "radarr") + encrypted_api_key
  |           test_cooldown.py:    (1, "sonarr"), (2, "radarr"), no encrypted_api_key
  |
  +-- test_settings           (AppSettings + auth reset)
        |
        +-- app               (TestClient)
        +-- async_client      (AsyncClient)
```

### How tests mock clients

Tests do NOT mock Python client classes. They mock at HTTP transport level
using `respx`:
- `@respx.mock` decorator intercepts all httpx calls
- Mocks per-URL/method:
  `respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(...)`
- `side_effect` lists for pagination
- Real client code executes fully (validates client-to-HTTP mapping)

Supervisor tests use `unittest.mock.patch` on `run_instance_search` itself
(testing task lifecycle, not search behavior).

### Factory functions in test_search_loop.py

```python
def _make_instance(
    instance_type=InstanceType.sonarr,
    instance_id=1,
    batch_size=2,
    hourly_cap=4,
    cooldown_days=14,
    unreleased_delay_hrs=36,
    sonarr_search_mode=SonarrSearchMode.episode,
) -> Instance
```

### Coupling to exactly-two-types

| Layer | Evidence |
|---|---|
| DB CHECK constraints | `('sonarr', 'radarr')` and `('episode', 'movie')` |
| Python enums | `InstanceType` has exactly 2 members |
| `seeded_instances` | Hard-codes 2 rows: id=1 sonarr, id=2 radarr |
| Assertions | `item_type == "episode"` for Sonarr, `"movie"` for Radarr |
| Factory functions | Default to `InstanceType.sonarr` |
| Dedicated tests | Sonarr-specific (episode + season_context), Radarr-specific (unreleased logic) |
| `test_cooldown_different_item_types_independent` | Explicitly tests "movie" vs "episode" as the only two types |

### What changes when refactoring

- Import paths for moved functions (label builders, unreleased checkers)
- Factory functions may need adapter registration
- `respx` mocks remain unchanged (HTTP calls don't change)
- New unit tests for adapter functions in isolation
- "Golden test" for identical `search_log` output

---

## Appendix 6: Security Boundary Map

```
                        EXTERNAL
                           |
                     [Rate Limiter]
                           |
                    [AuthMiddleware]
                     (session + CSRF)
                           |
                  +--------+--------+
                  |                 |
            [Settings Routes]  [API Routes]
            (URL validation)   (status, logs)
                  |
            [Instance CRUD]
            (encrypt API key)
                  |
               [Database]
            (encrypted at rest)
                  |
            [Instance Load]
            (decrypt API key)
                  |
            [Supervisor]
            (master_key in memory)
                  |
          [run_instance_search]
                  |
         [Client Construction]
         (plaintext key -> X-Api-Key header)
                  |
            [httpx.AsyncClient]
                  |
              OUTBOUND HTTP
          (to Sonarr/Radarr instance)
```

### Trust boundaries

1. **User -> Houndarr**: Auth, session, CSRF, rate limiting
2. **Houndarr -> DB**: Parameterized SQL, encrypted API keys
3. **Houndarr -> *arr instances**: SSRF guard at settings route only,
   `X-Api-Key` in headers, no TLS enforcement
4. **Logging boundary**: No secrets in logs or search_log table

### What the refactor touches

The refactor operates entirely within the `[run_instance_search]` and
`[Client Construction]` boxes. It does not touch:

- Authentication or session handling
- CSRF enforcement
- URL validation
- API key encryption/decryption
- Database schema
- Outbound HTTP security properties
- Logging boundary

The adapter functions receive already-decrypted `Instance` objects and
already-constructed client objects. They do not handle secrets, make HTTP
calls, or interact with the database. Their sole job is data
transformation (raw API response -> `SearchCandidate`) and search
dispatch (calling the existing client's `search()` method).

---

## Appendix 7: Existing Queue-Related Code

As of the base commit, there are exactly 3 occurrences of "queue" in the
codebase, none of which relate to Sonarr/Radarr download queues:

| File | Line | Context |
|---|---|---|
| `engine/supervisor.py:167` | Docstring for `trigger_run_now()` | "Queue one immediate search cycle" (verb) |
| `dashboard_content.html:173` | JavaScript UI label | `label.textContent = 'Queued'` (Run Now button state) |
| `tests/test_routes/test_logs.py:185` | Test fixture data | `"already queued"` reason string in seeded search_log |

Neither `SonarrClient` nor `RadarrClient` has any method for
`/api/v3/queue`. No code references the download queue concept.

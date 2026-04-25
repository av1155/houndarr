# Mock *arr Server

A single-process FastAPI mock that pretends to be Sonarr, Radarr, Lidarr,
Readarr, Whisparr v2, and Whisparr v3. Built for end-to-end testing of
Houndarr's search engine: the response shapes match the live *arr APIs,
seeded data is deterministic, and POSTed search commands are captured for
later assertion.

## Why

The live test *arr instances at `10.0.10.106:*` only carry a handful of
records. Houndarr's random search algorithm has different behaviour
regimes depending on `total_pages` vs `_MAX_LIST_PAGES_PER_PASS = 5`,
and the test instances are too small to exercise the regime that real
users sit in. This mock fills the gap by giving every app hundreds of
items at startup, configurable from the CLI.

## Run

```
just mock-arr                              # default: 500 items per app, seed 42, port 9100
just mock-arr port=9200 items=2000 seed=7  # heavier load, different seed
.venv/bin/python -m tests.mock_arr.server --port 9100 --items 500
```

The root URL prints a summary of seeded counts:

```
curl -s http://127.0.0.1:9100/ | jq .
```

Each app is mounted at `/<app>/api/v{1,3}/...`:

| App          | Base URL                              | API version |
|--------------|---------------------------------------|-------------|
| Sonarr       | `http://127.0.0.1:9100/sonarr`        | v3 |
| Radarr       | `http://127.0.0.1:9100/radarr`        | v3 |
| Lidarr       | `http://127.0.0.1:9100/lidarr`        | v1 |
| Readarr      | `http://127.0.0.1:9100/readarr`       | v1 |
| Whisparr v2  | `http://127.0.0.1:9100/whisparr_v2`   | v3 |
| Whisparr v3  | `http://127.0.0.1:9100/whisparr_v3`   | v3 |

Any `X-Api-Key` value (or none) is accepted.

## Endpoints implemented

Every app:
- `GET  /api/v{N}/system/status`
- `GET  /api/v{N}/queue/status`
- `POST /api/v{N}/command`

Sonarr / Whisparr v2:
- `GET /api/v3/wanted/missing` (paginated, `monitored`, `sortKey`, `sortDirection`, `includeSeries`)
- `GET /api/v3/wanted/cutoff`
- `GET /api/v3/series`
- `GET /api/v3/episode?seriesId={id}`

Radarr:
- `GET /api/v3/wanted/missing`
- `GET /api/v3/wanted/cutoff`
- `GET /api/v3/movie`

Lidarr / Readarr (v1):
- `GET /api/v1/wanted/missing` (with `includeArtist` / `includeAuthor`)
- `GET /api/v1/wanted/cutoff`
- `GET /api/v1/{artist,album}` or `/api/v1/{author,book}`

Whisparr v3 (no `/wanted` endpoints):
- `GET /api/v3/movie` (full library; Houndarr filters in memory)

Debug:
- `GET /__commands__/{sonarr|radarr|lidarr|readarr|whisparr_v2|whisparr_v3}` returns
  every command POSTed to that app since launch. Useful for asserting
  what the engine actually dispatched during a cycle.

## Wiring it into Houndarr

1. Start the mock: `just mock-arr items=500`
2. Start a Houndarr dev instance: `just dev` (uses `./data-dev`)
3. In the Houndarr UI, add six instances pointing at the mock URLs
   above. Any string works as the API key.
4. Enable random search order on whichever instance you want to study.
5. Watch the search log via `sqlite3 data-dev/houndarr.db` or the
   `/api/logs` route. Use the `/__commands__/{app}` debug endpoint to
   cross-check actual dispatch against the search log.

## Tuning record counts

The CLI takes a single `--items` knob that fans out to every app. To
study the small-library bias regime, drop `--items` low:

```
just mock-arr items=15  # forces N < 5 pages at pageSize=10
just mock-arr items=20  # N = 2 pages at pageSize=10
```

For per-app overrides, import `SeedConfig` from `tests.mock_arr.server`
and call `create_app(SeedConfig(...))` directly.

## Determinism

`--seed` controls every RNG draw: leaf-id assignment, parent partitioning,
the missing/cutoff/upgrade split, and the within-leaves shuffle that
decides which records end up in which bucket. Two runs with the same
`--seed` and `--items` produce byte-identical responses.

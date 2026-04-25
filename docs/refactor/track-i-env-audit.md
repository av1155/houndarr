# Track I.1: `os.getenv` / `os.environ` usage audit

Scope: every `.py` file under `src/`, excluding the two modules the
refactor plan carves out as the environment-variable boundary
(`src/houndarr/config.py` and `src/houndarr/__main__.py`).  The goal is
to confirm that no downstream module reaches around the
`AppSettings` / CLI boundary to read process env directly, and to list
anything that does so a consumer-migration batch (I.2) can pick it up.

## Method

```
grep -rn --include='*.py' -E 'os\.getenv|os\.environ|environ\[|environ\.get' src/
```

A broader `\bgetenv\b|\benviron\b` sweep across `src/`, `tests/`, and
`scripts/` was used to cross-check that no unusual idiom (for example
`from os import environ`) slipped past the first pattern.

## Finding

Zero hits in `src/` outside the two allowed files.  Every
environment-variable access inside the application source tree lives
inside `config.py` (reads) or `__main__.py` (writes).  The carved-out
boundary is intact; no consumer migration is required for I.2.

## In-scope reference: the boundary files

Listed so future audits can spot drift quickly.

### `src/houndarr/config.py` (reads; 10 direct call sites, 11 environment variables)

The table lists one row per environment variable plus one row for the
`_parse_bool_env` helper body itself (line 113).  The two rows marked
*via `_parse_bool_env`* are not additional `os.environ` call sites;
their values are resolved through the shared helper at line 113, which
is why the direct-call-site count (10) is one below the row count (12).

| Line | Env var                        | `AppSettings` field   |
| ---- | ------------------------------ | --------------------- |
| 113  | (helper body; `_parse_bool_env`) | n/a                 |
| 154  | `HOUNDARR_DATA_DIR`            | `data_dir`            |
| 155  | `HOUNDARR_HOST`                | `host`                |
| 156  | `HOUNDARR_PORT`                | `port`                |
| 157  | `HOUNDARR_DEV` (via `_parse_bool_env`) | `dev`         |
| 158  | `HOUNDARR_LOG_LEVEL`           | `log_level`           |
| 159  | `HOUNDARR_SECURE_COOKIES` (via `_parse_bool_env`) | `secure_cookies` |
| 160  | `HOUNDARR_COOKIE_SAMESITE`     | `cookie_samesite`     |
| 161  | `HOUNDARR_TRUSTED_PROXIES`     | `trusted_proxies`     |
| 162  | `HOUNDARR_AUTH_MODE`           | `auth_mode`           |
| 163  | `HOUNDARR_AUTH_PROXY_HEADER`   | `auth_proxy_header`   |
| 165  | `HOUNDARR_UPDATE_CHECK_REPO`   | `update_check_repo`   |

Recommended resolution: keep.  This is the one location allowed to
read process env; every field is documented on `AppSettings` and is the
canonical way for the rest of the app to consume the value.

### `src/houndarr/__main__.py` (writes; 8 call sites)

| Line | Env var                       |
| ---- | ----------------------------- |
| 174  | `HOUNDARR_DATA_DIR`           |
| 175  | `HOUNDARR_DEV`                |
| 176  | `HOUNDARR_LOG_LEVEL`          |
| 177  | `HOUNDARR_SECURE_COOKIES`     |
| 178  | `HOUNDARR_COOKIE_SAMESITE`    |
| 179  | `HOUNDARR_TRUSTED_PROXIES`    |
| 180  | `HOUNDARR_AUTH_MODE`          |
| 181  | `HOUNDARR_AUTH_PROXY_HEADER`  |

The writes propagate resolved CLI values to the process environment so
that uvicorn's reload child process (which re-imports modules fresh and
loses the `config._runtime_settings` module global) still resolves the
right values via `get_settings()`'s env-var fallback.

Note the three omissions from the write pass: `HOUNDARR_HOST` and
`HOUNDARR_PORT` (uvicorn's reload child receives these as arguments to
`uvicorn.run()`, not via `get_settings()`), and
`HOUNDARR_UPDATE_CHECK_REPO` (no CLI flag exists; the variable is
env-only by design so forks can redirect the update check without a
code change).

Recommended resolution: keep.  Any tightening here belongs to I.2's
`bootstrap_settings(**overrides)` collapse rather than to I.1.

## Out of scope: references outside `src/`

Recorded here only so nothing gets lost; none of these are consumer
migrations for Track I.

### `tests/e2e_browser/conftest.py` (5 reads)

| Line | Env var           | Purpose                                       |
| ---- | ----------------- | --------------------------------------------- |
| 21   | `HOUNDARR_URL`    | Playwright base URL override                  |
| 22   | `MOCK_SONARR_URL` | Mock Sonarr URL override                      |
| 23   | `MOCK_RADARR_URL` | Mock Radarr URL override                      |
| 24   | `HOUNDARR_E2E_USER` | Admin username for the browser flows        |
| 25   | `HOUNDARR_E2E_PASS` | Admin password for the browser flows        |

Recommended resolution: keep as documented exception.  These are
test-rig knobs used by the Playwright fixtures to point at whichever
mock stack the tester is running; they have no production surface and
no place on `AppSettings`.

### `scripts/marketing/seed_demo_data.py` (1 write)

| Line | Env var             |
| ---- | ------------------- |
| 74   | `HOUNDARR_DATA_DIR` |

Recommended resolution: defer to Track H.  Batch H.2
(`refactor(scripts): migrate scripts/marketing/seed_demo_data.py to use
bootstrap_non_web`) covers this script directly.  After H.2 the write
stays if the script still needs to set `HOUNDARR_DATA_DIR` for a
reload-capable sub-import path; otherwise it is replaced by a direct
`bootstrap_non_web(data_dir=...)` call.

### `scripts/marketing/serve_demo.py` (1 write)

| Line | Env var             |
| ---- | ------------------- |
| 52   | `HOUNDARR_DATA_DIR` |

Recommended resolution: defer to Track H.  Batch H.3
(`refactor(scripts): migrate scripts/marketing/serve_demo.py to use
bootstrap + document supervisor no-op patch`) covers this one.  Same
treatment as the seed script.

## Consequences for I.2

Because `src/` has no consumers to switch, the I.2 batch
(`refactor(config): collapse _runtime_settings override into
bootstrap_settings(**overrides)`) is strictly internal to `config.py`.
It does not need to chase down callers.  The CLI remains the single
writer (via env vars) and `get_settings()` / future
`bootstrap_settings(**overrides)` remain the single reader.

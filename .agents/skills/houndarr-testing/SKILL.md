---
name: houndarr-testing
description: Houndarr's pytest patterns. Loads when reading or editing tests/ files. Covers the fixture dependency graph, the FK seeding pattern for cooldowns/search_log tests, the local _login() and CSRF helpers in route tests, and the auth-state reset in test_settings.
paths:
  - "tests/**"
---

# Houndarr testing patterns

## Framework

- **pytest + pytest-asyncio**: `asyncio_mode = "auto"`,
  `asyncio_default_fixture_loop_scope = "function"`,
  `addopts = "-q --tb=short"` (set in `pyproject.toml`).
- **Async tests**: use `@pytest.mark.asyncio()` (with parens), return `-> None`.
- **HTTP mocking**: `respx` for httpx calls; use `@respx.mock` decorator.
- **App testing**: `TestClient` (sync) or `AsyncClient` via `ASGITransport`.

## Fixture dependency graph

```
tmp_data_dir          (temp directory, no deps)
  ├── db              (init SQLite, depends on tmp_data_dir)
  └── test_settings   (AppSettings + auth state reset, depends on tmp_data_dir)
        ├── app       (TestClient, depends on test_settings)
        └── async_client (AsyncClient, depends on test_settings)
```

`db` and `test_settings` are **siblings**; both depend on `tmp_data_dir`
independently. Tests that need a database AND the app must request both
`db` and `app` (or use fixtures that depend on `db`).

The `test_settings` fixture resets `_auth._serializer`,
`_auth._setup_complete`, and `_auth._login_attempts` so auth state does
not bleed between tests.

## FK constraint pattern

Tests touching `cooldowns` or `search_log` must seed the `instances` table
first via the `seeded_instances` fixture (defined locally in
`tests/test_engine/test_search_loop.py`,
`tests/test_engine/test_golden_search_log.py`,
`tests/test_engine/test_supervisor.py`, and
`tests/test_services/test_cooldown.py`):

```python
@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key)"
            " VALUES (?, ?, ?, ?, ?)",
            [(1, "Sonarr Test", "sonarr", "http://sonarr:8989", _ENC_KEY),
             (2, "Radarr Test", "radarr", "http://radarr:7878", _ENC_KEY)],
        )
        await conn.commit()
    yield
```

Engine tests set `encrypted_api_key` to a valid Fernet-encrypted value
(`_ENC_KEY`). The simpler 4-column form (without `encrypted_api_key`)
is used in `test_cooldown.py` where only FK constraints matter.

## Login helper for route tests

A `_login()` helper is defined locally in each route test file that needs
it (`test_logs.py`, `test_settings.py`, `test_status.py`):

```python
def _login(client: TestClient) -> None:
    client.post("/setup", data={"username": "admin", "password": "ValidPass1!", ...})
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})
```

## CSRF helper for route tests

Mutating authenticated routes require a valid CSRF token. Use the helpers
from `tests/conftest.py`:

```python
from tests.conftest import csrf_headers, get_csrf_token

resp = client.post("/settings/instances", data=form, headers=csrf_headers(client))
resp = client.delete("/settings/instances/1", headers=csrf_headers(client))
```

Current CSRF exemptions: `POST /logout`, `/login`, `/setup`.

## Markers

- `@pytest.mark.integration`: 12 async engine-cycle cases in `tests/test_e2e/`
  plus 15 Playwright flows in `tests/e2e_browser/` (browser tree excluded
  from default collection via `norecursedirs`; `test_e2e/` is collected and
  filterable).
- `@pytest.mark.pinning`: characterisation tests pinning current behaviour
  before a refactor batch. Unit-scope; runs in the default suite. Add one
  whenever a refactor needs a behavioural lock.

## Running

`just test`, `just test-quick`, `just test-integration`, and `just pin` run
with `pytest -n auto` by default (pytest-xdist). Override with
`PYTEST_WORKERS=0` for serial triage, or `PYTEST_WORKERS=4` to constrain.
`just test-browser chromium` runs the Playwright e2e flows serially
(shared stack on fixed ports).

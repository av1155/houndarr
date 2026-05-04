---
name: houndarr-python
description: Houndarr-specific Python conventions on top of the global python skill. Loads when reading or editing .py files in this repo. Covers the noqa/nosec suppression table, the AppSettings-as-plain-dataclass decision, the frozen-dataclass-with-slots invariant for domain models, the per-module logger pattern, and the background-task error-handling shape.
paths:
  - "**/*.py"
---

# Houndarr-specific Python conventions

Apply alongside the global `python` skill. The global skill covers PEP 604
unions, lowercase generics, mypy strict, naming, etc. This skill adds the
Houndarr-specific decisions that are not generic Python good-practice.

## Imports

`from __future__ import annotations` is mandatory in every `.py` file that
contains code. Empty `__init__.py` package markers are exempt. isort runs
via Ruff with `known-first-party = ["houndarr"]`.

## Naming conventions

| Kind | Style | Example |
|------|-------|---------|
| Classes / dataclasses | PascalCase | `SonarrClient`, `AppSettings` |
| Functions / methods | snake_case | `create_instance`, `run_instance_search` |
| Private helpers | `_leading_underscore` | `_write_log`, `_render` |
| Constants | UPPER_SNAKE_CASE | `SESSION_MAX_AGE_SECONDS`, `SCHEMA_VERSION` |
| Module-level state | `_leading_underscore` | `_db_path`, `_runtime_settings` |
| Enums | `StrEnum`, lowercase values | `InstanceType.sonarr` |
| Type aliases | PascalCase or Literal | `RunNowStatus = Literal["accepted", "not_found", "disabled"]` |

## Dataclass invariants

- **`AppSettings` is a plain dataclass, not Pydantic.** Pydantic is used only
  at the *arr wire boundary (`src/houndarr/clients/_wire_models/`), never for
  internal domain models or config. `get_settings()` is a lazy singleton and
  the only deliberately-mutable dataclass (env overrides applied in-place).
- **Every frozen dataclass uses `slots=True`.** Domain models (`MissingEpisode`,
  `LibraryMovie`, etc.) live next to the client that builds them; `Instance`
  composes seven frozen sub-structs and is itself frozen and slotted. Callers
  evolve through `dataclasses.replace`.

## Logging

Every module that logs uses `logger = logging.getLogger(__name__)` at module
level. Root logger is configured in `__main__.py` via `logging.basicConfig()`.
No alternative logging libraries (structlog, loguru) are used.

## Error handling shape

- **Background tasks:** `except asyncio.CancelledError: raise` first, then
  broad `except Exception` with `# noqa: BLE001`; log + continue/retry.
- **HTTP clients:** `response.raise_for_status()` in `_get()` / `_post()`;
  callers catch `httpx.HTTPError` or `httpx.TransportError`.
- **Auth helpers:** catch-all returns `False` (never leaks info).
- **Routes:** return re-rendered templates with `status_code=422` for
  validation errors; use `HTTPException` in API routes.

## Docstrings

- Module-level docstring on every file that contains code.
- Google-style for functions: `Args:`, `Returns:`, `Raises:` sections.
- Test functions may use brief single-line docstrings.

## Comments

Read [`docs/commenting-standard.md`](../../../docs/commenting-standard.md)
at least once per session before writing or editing code in this repo. It
codifies the full commenting standard (per-language rules for Python,
HTML/Jinja2, CSS, JS, SQL, YAML, shell, Markdown) plus the universal
principles. Core rule: **comments explain _why_, code explains _what_**.

## Known `noqa` / `nosec` suppressions

| Code | Reason |
|------|--------|
| `SIM117` | Nested `async with` required by aiosqlite pattern |
| `S104` | Intentional bind to `0.0.0.0` for self-hosted server |
| `B008` | FastAPI `Depends()` in function defaults |
| `S608` + `nosec` | Dynamic SQL with explicit column allowlist (4 files). Use bare `# nosec` (no test ID) per [bandit#1204](https://github.com/PyCQA/bandit/issues/1204): the test-ID form emits a spurious `WARNING nosec encountered (B608), but no failed test` per f-string interpolation on bandit 1.7.3+ |
| `BLE001` | Broad exception in background loops (always with logging) |
| `A002` | Parameter names `type` / `id` shadowing builtins (FastAPI form / function signature convention) |
| `SLF001` | Test fixtures and `__main__.py` accessing private module state |
| `PLW0603` | Module-level global reassignment (singletons); the `PLW` rule family is not currently selected in ruff config, so these comments are defensive / inert |
| `S101` | Defensive assert in adapters and instance validation; also globally ignored in ruff config. Per-file comments are defensive. |

## Type annotations (Houndarr-specific reminders)

- Tests are exempt from `ANN` rules (per-file-ignores in `pyproject.toml`).
- `collections.abc.AsyncGenerator`, not `typing.AsyncGenerator`.
- Specific error codes: `# type: ignore[assignment]`; never bare `# type: ignore`.

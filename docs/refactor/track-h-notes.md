# Track H notes

Short notes on non-obvious choices made while sharing the non-web
bootstrap across Houndarr's three entry points (the CLI, the demo
seed script, and the demo serve script).

## Supervisor no-op patch in serve_demo.py

`scripts/marketing/serve_demo.py` replaces the engine's
`run_instance_search` at two module attribute sites before uvicorn
starts:

- `houndarr.engine.search_loop.run_instance_search`, the definition.
- `houndarr.engine.supervisor.run_instance_search`, the reference the
  supervisor actually calls. Python binds the name at import time, so
  patching the definition alone leaves the supervisor holding a stale
  reference.

The patch is intentionally script-local and is not hoisted into
`bootstrap_non_web`. The CLI (`python -m houndarr`) and the FastAPI
lifespan both expect the real supervisor to run. Only the marketing
script suppresses it so the dashboard renders the seeded snapshot
values cleanly for screenshot capture, without indexer errors from
the fake `http://sonarr:8989` URLs bleeding through.

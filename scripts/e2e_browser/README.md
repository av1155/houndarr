# scripts/e2e_browser

Bash orchestrator for the browser end-to-end suite under
`tests/e2e_browser/`.

- `just e2e-up` — build the `houndarr:e2e` image if absent, create
  the `arr-net` docker network, start mock-sonarr + mock-radarr +
  houndarr-e2e, wait for Houndarr health.
- `just e2e-down` — tear the whole thing down.

Both route through `capture_baselines.sh`, which takes a mode
argument (`up` or `down`).  Read the script header for the defaults
and the environment-variable knobs.

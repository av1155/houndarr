# scripts/e2e_browser

Bash orchestrator for the browser end-to-end suite under
`tests/e2e_browser/`.

- `just e2e-up`: build the `houndarr:e2e` image (Docker's layer cache
  keeps an unchanged rebuild fast), create the `arr-net` docker
  network, start mock-sonarr + mock-radarr + houndarr-e2e, wait for
  Houndarr health, and create the test admin account.
- `just test-browser <browser>`: run the suite against that stack.
- `just e2e-down`: tear the whole thing down.

Both route through `capture_baselines.sh`, which takes a mode
argument (`up` or `down`).  Read the script header for the defaults
and the environment-variable knobs.

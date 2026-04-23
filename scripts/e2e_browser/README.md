# scripts/e2e_browser

Bash orchestrators for the browser end-to-end suite under
`tests/e2e_browser/`.  The full documentation lives next to the
captured PNG baselines at
[`tests/e2e_browser/_screenshots/README.md`](../../tests/e2e_browser/_screenshots/README.md).

Short version:

- `just e2e-up` — build the `houndarr:e2e` image if absent, create
  the `arr-net` docker network, start mock-sonarr + mock-radarr +
  houndarr-e2e, wait for Houndarr health.
- `just e2e-down` — tear the whole thing down.
- `just capture-baselines` — one-shot capture flow (fresh /data,
  capture `/setup`, create admin, capture `/login`, tear down).
- `just verify-baselines` — same flow but without
  `--update-snapshots`; the committed PNGs must satisfy the
  assertion.

Everything routes through `capture_baselines.sh`, which takes a mode
argument (`up`, `down`, `capture`, `verify`).  Read the script header
for the defaults and the environment-variable knobs.

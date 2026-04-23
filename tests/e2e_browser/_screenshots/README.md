# Playwright reference screenshots

PNG baselines for the browser end-to-end suite at
`tests/e2e_browser/test_flows.py`.  Phase 7b of the final refactor wave
added two visual pins here: `test_login_page_visual` and
`test_setup_page_visual`.  Every run of `just test-browser chromium`
compares live renders against these committed PNGs.

## Capturing baselines

A baseline capture requires the full mock `*arr` stack (mock-sonarr +
mock-radarr + houndarr-e2e on a dedicated `arr-net` docker network).
The orchestration is packaged as a single reproducible command:

```bash
just capture-baselines
```

This runs `scripts/e2e_browser/capture_baselines.sh capture`, which:

1. Builds `houndarr:e2e` from the repo root `Dockerfile` if the image
   is absent.
2. Creates the `arr-net` docker network.
3. Starts mock-sonarr (port 8989) and mock-radarr (port 7878) on the
   network using the shared `mock_arr.py` script.
4. Starts `houndarr-e2e` with a fresh `/tmp/houndarr-e2e-data` volume
   (pre-admin state) on host port `8877`.
5. Waits for `/api/health` to return `{"status":"ok"}`.
6. Runs pytest inside a Linux Playwright container with
   `-k test_setup_page_visual --update-snapshots` (captures the
   pre-setup `/setup` page).
7. Creates the admin account via `POST /setup`.
8. Runs pytest a second time inside the same container image with
   `-k test_login_page_visual --update-snapshots` (captures `/login`
   after logout).
9. Tears the stack down (containers + network + data dir).

The captured PNGs land under this directory with a
Playwright-generated filename of the form
`<test_name>_<os>_<browser>.png` (driven by the
`snapshot_path_template` configured in
`tests/e2e_browser/conftest.py`).

## Verifying without re-capturing

For a regular sanity check that the committed baselines still match a
freshly-rendered Houndarr, run:

```bash
just verify-baselines
```

Same two-pytest flow but without `--update-snapshots`; any pixel diff
fails the run.  This is also what `just test-browser chromium` exercises
when invoked against a running stack.

## When to re-capture

Re-capture is required when one of the following changes:

- `src/houndarr/templates/login.html` or `src/houndarr/templates/setup.html`
- `src/houndarr/static/css/auth.css`, `auth-fields.css`, or any token
  definition in `tokens.css` that they resolve through
- `src/houndarr/static/js/auth.js` if it mutates visible markup on load
- The compiled `app.built.css` (Tailwind upgrade, daisyUI upgrade, or
  other build-toolchain change)

Re-capture is NOT required when:

- Python route handlers change without touching the rendered HTML.
- Tests or fixtures in `tests/` change without altering the templates.
- Non-auth templates change (the two visual pins scope narrowly to
  `/login` and `/setup`).

Commit captured PNGs in the same commit as the change that produced
them, with a commit body that explains why the pixel output moved.

## OS parity: why we capture inside a Linux container

Playwright's chromium picks fonts via the OS font stack + HarfBuzz
shaping tables.  A PNG captured on macOS does not line up byte-for-byte
with the same page rendered on Ubuntu, even at the same viewport and
same Chromium build: font antialiasing and kerning differ at the pixel
level.  CI runs on `ubuntu-latest` via the
`.github/workflows/browser-e2e.yml` workflow, so every baseline we
commit MUST be captured in a matching Linux environment or the first
CI run rejects the PR on a spurious visual diff.

The capture script pins
`mcr.microsoft.com/playwright/python:v1.58.0-jammy` — the Linux image
tagged for the same `playwright==1.58.0` version pinned in
`tests/e2e_browser/requirements.txt`.  Running pytest inside that
container guarantees the captured PNGs match CI pixel-for-pixel.

The alternative (capturing directly on the host and configuring a
`maxDiffPixels` threshold in the test) was deliberately rejected: a
threshold erodes a strict byte-equal assertion into a fuzzy one and
still requires re-tuning whenever the font stack or Chromium build
moves.  The container detour keeps the assertion strict.

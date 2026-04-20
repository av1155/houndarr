# Marketing screenshot tooling

Scripts that regenerate the dashboard, logs, settings, and help
screenshots embedded in `website/static/img/screenshots/` and
`docs/images/`. Useful when the UI changes and every page on the
website needs a fresh capture.

## What's here

| File | Purpose |
|------|---------|
| `seed_demo_data.py` | Fills a scratch data dir with demo instances, cooldowns, search log rows, and an admin user |
| `serve_demo.py` | Runs `uvicorn` against a seeded data dir with the supervisor's search loop patched to a no-op |
| `capture_screenshots.py` | Drives Playwright through every documented view and writes the PNG + JPEG pair each page references |
| `demo_titles/` | TV, movies, albums, books pools the seed script draws from. Edit these to refresh the fictional library without touching Python |

All three scripts default to `./marketing-data/` for the scratch DB
(ignored by `.gitignore`) and `http://127.0.0.1:8902` for the server.

## Prerequisites

- Project venv with dev dependencies installed: `pip install -r requirements-dev.txt && pip install -e .`
- Playwright browsers: `.venv/bin/playwright install chromium`

## Full regeneration (populated + empty, one command pass each)

```bash
# 1. Populated state — covers every view except the empty-dashboard hero shot.
.venv/bin/python scripts/marketing/seed_demo_data.py --mode populated
.venv/bin/python scripts/marketing/serve_demo.py &                  # background
.venv/bin/python scripts/marketing/capture_screenshots.py           # --seed-mode populated
kill %1

# 2. Empty state — only the empty-dashboard view.
.venv/bin/python scripts/marketing/seed_demo_data.py --mode empty
.venv/bin/python scripts/marketing/serve_demo.py &
.venv/bin/python scripts/marketing/capture_screenshots.py --seed-mode empty
kill %1
```

The scripts write to the canonical locations the docs already reference
(`website/static/img/screenshots/*.png` and `docs/images/*.jpeg`), so
the website + README pick up new shots on the next build with no path
changes.

## Capturing a single view

```bash
.venv/bin/python scripts/marketing/capture_screenshots.py --views logs settings-help
```

View names: `dashboard`, `dashboard-empty`, `logs`, `logs-mobile`,
`settings-instances`, `settings-account`, `settings-help`,
`add-instance`.

## Refreshing the demo library

Edit the JSON files under `demo_titles/`. Each entry is
`[item_id, "Display label"]`; keep `item_id` unique across the four
files (the seed script uses these as `cooldowns.item_id` and
`search_log.item_id`).

## Adding a new view

1. Append a `View(...)` to the `VIEWS` list in `capture_screenshots.py`.
2. Pick a `wait_selector` that's present on the page once it has
   hydrated (prefer stable IDs or data attributes over class names that
   exist during loading states).
3. If the view needs an interaction (click a tab, expand a `<details>`,
   open a modal), add a branch to `_prepare_view()`.
4. Drop the expected PNG + JPEG filenames so the paths align with where
   the docs already reference them.

## Notes

- The scratch DB is safe to reuse across runs; `seed_demo_data.py`
  clears demo tables before seeding and preserves the Fernet master key.
- The server patches `run_instance_search` to a no-op, so the supervisor
  won't spam `active_error` banners trying to reach the fake
  `http://sonarr:8989` URLs. The backfilled `snapshot_refreshed_at`
  column makes the dashboard think the snapshot is fresh.
- Screenshots use `device_scale_factor=1.67` and `color_scheme=dark` to
  match the existing retina + dark-theme renderings in the repo.

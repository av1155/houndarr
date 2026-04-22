# Playwright reference screenshots

Track A.24 of the refactor plan.  This directory holds PNG baselines captured
from the Playwright flows in `tests/e2e_browser/test_flows.py` before Track E
(Jinja macros) and Track G (Tailwind `@layer components`) land their changes.

Track E.18 and Track G.12 gates run Playwright again and compare the freshly
captured PNGs against these baselines.  Any pixel diff requires a recorded
rationale in the commit body.

## Capturing baselines

Baselines are captured with the mock `*arr` stack running.  From the repo
root:

```bash
# Build the image (only once, or after Dockerfile changes)
docker build -t houndarr:test .

# Run the mock stack + Houndarr on a dedicated bridge network
docker run -d --rm --name mock-sonarr --network arr-net \
    --entrypoint python houndarr:test \
    tests/e2e_browser/mock_arr.py --app Sonarr --port 8989

docker run -d --rm --name mock-radarr --network arr-net \
    --entrypoint python houndarr:test \
    tests/e2e_browser/mock_arr.py --app Radarr --port 7878

docker run -d --rm --name houndarr --network arr-net -p 8877:8877 \
    -v $(pwd)/data-e2e:/data houndarr:test

# Capture baselines
just test-browser chromium
```

PNGs land in this directory.  Commit them as the pinning reference.

## Coverage flows (from test_flows.py)

- `test_setup_flow`: first-run /setup page, password form, redirect to /login.
- `test_login_flow`: login success, dashboard landing.
- `test_dashboard_layout`: empty dashboard and with one instance.
- `test_settings_add_instance`: full add-instance modal + Test Connection.
- `test_settings_edit_instance`: edit form pre-populated state.
- `test_settings_toggle_enabled`: toggle state + HTMX row swap.
- `test_settings_delete_instance`: confirm dialog + row removal.
- `test_logs_page_empty`: empty-state row + filters.
- `test_logs_page_populated`: populated table + pagination button.
- `test_admin_factory_reset`: confirm dialog + typed-phrase guard.
- `test_admin_reset_instances`: flash toast.
- `test_admin_clear_logs`: flash toast.
- `test_changelog_popup_manual`: manual re-open.
- `test_update_check_refresh`: refresh button + result badge.
- `test_account_password_change`: happy path + HX-Refresh.

If the refactor in Track E / G adds new flows, capture matching baselines in
the same pass so the diff check stays total-coverage.

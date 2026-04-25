# Route-handler LOC histogram (Track D.28)

Measured at the close of Track D: every handler body in
`src/houndarr/routes/` is well under the 200-line soft cap declared in
plan section 5. Max body LOC is 95 (`admin_factory_reset`), median is 8,
and the long tail sits comfortably below 70 LOC.

Body LOC here means the count of source lines inside the `def ...`
block, excluding the leading docstring and blank tail. Decorator
lines, signature lines, and the closing `)` of long argument lists
are not counted. The pinning test
`tests/test_routes/test_handler_loc.py` enforces the cap statically
via AST walk; the numbers below are the snapshot at the D.28 commit.

| LOC | Handler | File |
| --- | ------- | ---- |
| 95 | `admin_factory_reset` | `routes/admin.py` |
| 66 | `account_password_update` | `routes/settings/account.py` |
| 59 | `logs_page` | `routes/pages.py` |
| 49 | `instance_create` | `routes/settings/instances.py` |
| 47 | `instance_update` | `routes/settings/instances.py` |
| 43 | `popup` | `routes/changelog.py` |
| 38 | `setup_post` | `routes/pages.py` |
| 30 | `get_logs_partial` | `routes/api/logs.py` |
| 26 | `login_post` | `routes/pages.py` |
| 23 | `instance_toggle_enabled` | `routes/settings/instances.py` |
| 10 | `admin_reset_instances` | `routes/admin.py` |
| 10 | `instance_test_connection` | `routes/settings/instances.py` |
| 10 | `instance_edit_get` | `routes/settings/instances.py` |
| 8 | `run_now` | `routes/api/status.py` |
| 8 | `instance_add_form` | `routes/settings/instances.py` |
| 8 | `instance_delete` | `routes/settings/instances.py` |
| 7 | `preferences` | `routes/update_check.py` |
| 6 | `admin_clear_logs` | `routes/admin.py` |
| 6 | `settings_help_page` | `routes/pages.py` |
| 6 | `status` | `routes/update_check.py` |
| 6 | `refresh` | `routes/update_check.py` |
| 4 | `dashboard` | `routes/pages.py` |
| 3 | `get_status` | `routes/api/status.py` |
| 3 | `disable` | `routes/changelog.py` |
| 3 | `preferences` | `routes/changelog.py` |
| 3 | `setup_get` | `routes/pages.py` |
| 3 | `login_get` | `routes/pages.py` |
| 3 | `logout` | `routes/pages.py` |
| 2 | `get_logs` | `routes/api/logs.py` |
| 2 | `dismiss` | `routes/changelog.py` |
| 1 | `health` | `routes/health.py` |
| 1 | `settings_get` | `routes/settings/page.py` |

Notes on the outliers:

- `admin_factory_reset` at 95 LOC is the widest handler. It still has
  non-trivial orchestration (supervisor shutdown, file deletion, auth
  cache reset, supervisor re-init, retention-loop respawn) that a
  further extraction would fragment across multiple service modules
  without making the request pipeline any clearer. Leaving it as is.
- `account_password_update` and `logs_page` carry form validation and
  template context respectively; both are readable and well under cap.
- `instance_create` and `instance_update` are essentially kwarg
  forwarders into `submit_create` / `submit_update`. Their size is
  driven by the 26-field form surface, not by logic.

The 200-line soft cap is enforced statically by
`tests/test_routes/test_handler_loc.py`. A new handler that bumps
past the cap fails the test with a clear failure message that names
the offending file and handler.

"""Browser end-to-end flows driven by pytest-playwright.

Parametrised by the ``--browser`` flag; the workflow runs chromium,
firefox, and webkit as separate matrix jobs.  Console errors and page
errors are caught by an autouse fixture in ``conftest.py``.
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import suppress
from pathlib import Path

from playwright.sync_api import Locator, Page, expect

_SCREENSHOTS_DIR = Path(__file__).resolve().parent / "_screenshots"

# Admin credentials used by the self-healing setup-page capture below.
# Values mirror ``tests/e2e_browser/conftest.py:23-24`` so the same
# env-var overrides apply when CI or a maintainer re-keys the admin.
_ADMIN_USER = os.environ.get("HOUNDARR_E2E_USER", "admin")
_ADMIN_PASS = os.environ.get("HOUNDARR_E2E_PASS", "CITestPass1!")


def _assert_screenshot(page: Page, name: str) -> None:
    """Byte-compare a page screenshot against the committed baseline.

    Baselines live at ``tests/e2e_browser/_screenshots/<name>``.  When the
    ``HOUNDARR_E2E_CAPTURE=1`` environment variable is set (the
    capture script sets it on the first pytest invocation pass), the
    current screenshot is written to disk instead of compared.  On a
    verification run the env var stays unset and any pixel diff fails
    the test.  On mismatch, the actual bytes are saved alongside as
    ``<name>.actual.png`` so the maintainer can open both files in any
    image viewer and eyeball the delta before deciding whether to
    re-capture.
    """
    path = _SCREENSHOTS_DIR / name
    actual = page.screenshot(full_page=True, type="png")
    if os.environ.get("HOUNDARR_E2E_CAPTURE") == "1":
        _SCREENSHOTS_DIR.mkdir(exist_ok=True)
        path.write_bytes(actual)
        return
    if not path.exists():
        raise AssertionError(
            f"baseline missing: {path}.  Run `just capture-baselines` to create it."
        )
    expected = path.read_bytes()
    if actual == expected:
        return
    actual_path = path.with_suffix(".actual.png")
    actual_path.write_bytes(actual)
    raise AssertionError(
        f"pixel diff vs {path.name}; actual saved to {actual_path.name} for inspection"
    )


def _wait_for_connection_ui_idle(page: Page) -> None:
    """Drain the 80 ms setTimeout scheduled by the field-change handler.

    The settings JS reacts to ``input``/``change`` events on the
    type/url/api_key fields by adding ``is-updating`` to
    ``#instance-connection-status`` and scheduling a ~80 ms text reset.
    Two places trigger it in this flow: ``locator.fill()`` (synchronous
    input events) and the blur that happens when clicking Test Connection
    (which fires ``change`` on the previously focused input).  When the
    mock *arr runs on the same Docker network the HTMX round-trip is
    faster than 80 ms, so the stale timer wipes out the success message
    if we don't drain it on both sides of the click.
    """
    page.wait_for_function(
        "() => !document.querySelector('#instance-connection-status')"
        "?.classList.contains('is-updating')"
    )


def _wait_for_htmx_idle(page: Page) -> None:
    """Wait until all HTMX request/swap/settle classes are gone.

    Follows the maintainer-suggested pattern from bigskysoftware/htmx
    discussion #2360 for reliable Playwright assertions after HTMX.
    """
    expect(
        page.locator(".htmx-request, .htmx-settling, .htmx-swapping, .htmx-added")
    ).to_have_count(0)


def _test_connection_and_wait_for_success(page: Page, form: Locator, button: Locator) -> None:
    """Trigger Test Connection and wait for the success signal.

    Two races would otherwise make this flaky against a fast mock:

    1. ``locator.fill()`` fires ``input`` events that schedule an 80 ms
       ``setTimeout`` which resets ``#instance-connection-status``.
       A real button click then fires a ``change`` event on the
       previously focused input, scheduling another reset that races
       with the HTMX response.  We dispatch the click synthetically so
       no blur/change happens, and drain any residual timer afterwards.
    2. The HTMX ``HX-Trigger`` + DOM swap + any pending reset timer must
       all settle before we assert.  The submit button's enabled state
       is the authoritative success signal: the JS handler for
       ``houndarr-connection-test-success`` sets ``connection_verified``
       and enables submit regardless of text-swap timing.

    The response filter matches any status for the test-connection URL.
    Filtering on ``status == 200`` would hang for the full Playwright
    timeout on a 422 (connection failure) and surface as an opaque
    wait-timeout instead of a legible status code.
    """
    with page.expect_response(
        lambda r: "/settings/instances/test-connection" in r.url
    ) as resp_info:
        button.dispatch_event("click")
    resp = resp_info.value
    assert resp.status == 200, f"test-connection returned {resp.status}: {resp.text()}"
    _wait_for_connection_ui_idle(page)
    _wait_for_htmx_idle(page)
    expect(form.locator("#instance-submit-btn")).to_be_enabled(timeout=10_000)


def _submit_form(form: Locator) -> None:
    """Submit the form via ``HTMLFormElement.requestSubmit``.

    Clicking the submit button fires a blur/change event on the
    previously focused field, which the settings JS interprets as
    ``connection details changed`` and disables the submit button
    before the native browser can forward the click to the form's
    submit handler.  ``requestSubmit`` bypasses the click chain and
    dispatches a real ``submit`` event that HTMX intercepts, without
    the blur side effect.
    """
    form.evaluate("form => form.requestSubmit()")


def _open_admin_dropdown(page: Page) -> None:
    """Click the Admin toggle and wait for the body to finish animating open.

    Commit fdeeb74 made ``#admin-body`` start at ``height:0; opacity:0``
    on every page load, so any test that clicks into an element nested
    inside the dropdown needs to open it first. Playwright would
    otherwise wait 30s for a zero-height element to become actionable
    and fail with ``pointer events intercepted``.
    """
    panel = page.locator("#admin-grouped")
    if panel.get_attribute("data-open") != "true":
        page.locator("#admin-toggle").click()
    expect(panel).to_have_attribute("data-open", "true")


def test_full_instance_lifecycle(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """Add an instance against the mock *arr, then flip search_order and verify persistence.

    Combines add and edit into one linear user story so the test does
    not depend on any sibling test's state.

    The instance name is randomised per invocation so that a
    ``pytest-rerunfailures`` retry (the CI job runs ``--reruns 2``) does
    not collide with the row the previous attempt left behind.  The
    edit-flow row lookup then targets the row by this unique name
    instead of ``.first``, so retries remain idempotent.
    """
    page = logged_in_page
    instance_name = f"E2E Sonarr {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")

    # Open the add modal.
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    expect(add_form).to_be_visible()

    # Fill in the form against the mock Sonarr.
    add_form.locator('input[name="name"]').fill(instance_name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-sonarr-key")
    _wait_for_connection_ui_idle(page)

    # Test Connection must succeed before Save is enabled.
    _test_connection_and_wait_for_success(
        page, add_form, add_form.locator("button[data-test-connection-btn]")
    )

    # Save.
    _submit_form(add_form)
    expect(page.locator("#instance-tbody")).to_contain_text(instance_name, timeout=10_000)

    # Re-open to verify the default persisted as Random.  Scope the
    # row lookup by the unique name so retries cannot open the wrong row.
    row = page.locator("#instance-tbody tr").filter(has_text=instance_name)
    row.locator('button[hx-get^="/settings/instances/"]').click()
    edit_form = page.locator('form[data-form-mode="edit"]')
    expect(edit_form).to_be_visible()
    expect(edit_form.locator('select[name="search_order"]')).to_have_value("random")

    # Flip to Chronological.  The edit form always starts with
    # connection_verified=false, so re-run the connection test first.
    edit_form.locator('select[name="search_order"]').select_option("chronological")
    _wait_for_connection_ui_idle(page)
    _test_connection_and_wait_for_success(
        page, edit_form, edit_form.locator("button[data-test-connection-btn]")
    )
    _submit_form(edit_form)
    expect(edit_form).to_be_hidden(timeout=10_000)

    # Re-open the same row and verify persistence.
    row.locator('button[hx-get^="/settings/instances/"]').click()
    reopened = page.locator('form[data-form-mode="edit"]')
    expect(reopened).to_be_visible()
    expect(reopened.locator('select[name="search_order"]')).to_have_value("chronological")


# ---------------------------------------------------------------------------
# 4xx error surface regression guards
#
# HTMX 2.x defaults ``responseHandling`` to ``swap: false`` for 4xx/5xx.
# Houndarr's routes return 422 with ``HX-Retarget`` / ``HX-Reswap`` /
# ``HX-Trigger`` headers and a rendered error body; those headers are
# no-ops unless the client config says 4xx should swap.  ``base.html``
# overrides ``htmx.config.responseHandling`` via a meta tag so the
# server-emitted retargets actually land.  Each test below triggers a
# different 4xx surface and asserts the error body is visible, so a
# future meta-tag edit can't silently regress the whole app to silent
# failure again.
# ---------------------------------------------------------------------------


_EXPECTED_422_CONSOLE_NOISE = [
    r"Failed to load resource: the server responded with a status of 422",
    r"Response Status Error Code 422",
]


def test_save_instance_4xx_renders_error(
    logged_in_page: Page,
    houndarr_url: str,
    mock_sonarr_url: str,
    console_guard,
) -> None:
    """Saving the add form without a verified connection should render the
    server-provided error text in ``#instance-connection-status``."""
    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    expect(add_form).to_be_visible()

    add_form.locator('input[name="name"]').fill(f"E2E Guard {uuid.uuid4().hex[:8]}")
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("guard-key")
    _wait_for_connection_ui_idle(page)

    # requestSubmit with ``connection_verified=false`` in the hidden input
    # triggers the 422 guard path in routes/settings/instances.py
    # (instance_create, the connection_verified != "true" branch).
    with page.expect_response(
        lambda r: r.url.endswith("/settings/instances") and r.request.method == "POST"
    ) as resp_info:
        _submit_form(add_form)
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_htmx_idle(page)

    expect(page.locator("#instance-connection-status")).to_contain_text(
        "Test connection successfully before adding.",
        timeout=5_000,
    )


def test_test_connection_4xx_renders_error(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """Test Connection against an SSRF-blocked URL (loopback) should render
    the server-provided error text in ``#instance-connection-status``."""
    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    expect(add_form).to_be_visible()

    add_form.locator('input[name="name"]').fill(f"E2E SSRF {uuid.uuid4().hex[:8]}")
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill("http://127.0.0.1")
    add_form.locator('input[name="api_key"]').fill("guard-key")
    _wait_for_connection_ui_idle(page)

    with page.expect_response(
        lambda r: "/settings/instances/test-connection" in r.url
    ) as resp_info:
        add_form.locator("button[data-test-connection-btn]").dispatch_event("click")
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_connection_ui_idle(page)
    _wait_for_htmx_idle(page)

    expect(page.locator("#instance-connection-status")).to_contain_text(
        "blocked address range",
        timeout=5_000,
    )


def test_password_change_4xx_renders_error(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """Submitting the password form with the wrong current password should
    render the admin-security error in-place."""
    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    section = page.locator("#admin-security")
    expect(section).to_be_visible()

    pw_form = section.locator('form[hx-post="/settings/account/password"]')
    pw_form.locator('input[name="current_password"]').fill("WrongOldPass1!")
    pw_form.locator('input[name="new_password"]').fill("NewValidPass1!")
    pw_form.locator('input[name="new_password_confirm"]').fill("NewValidPass1!")

    with page.expect_response(
        lambda r: "/settings/account/password" in r.url and r.request.method == "POST"
    ) as resp_info:
        pw_form.evaluate("f => f.requestSubmit()")
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_htmx_idle(page)

    expect(page.locator("#admin-security")).to_contain_text(
        "Current password is incorrect.",
        timeout=5_000,
    )
    # Focus is restored to the first password input so keyboard users do
    # not land on document.body after the submit button is replaced.
    expect(page.locator("#current-password")).to_be_focused(timeout=5_000)


# ---------------------------------------------------------------------------
# Admin dropdown coverage (Security / Updates / Maintenance / Danger zone)
# ---------------------------------------------------------------------------


def test_admin_dropdown_toggle_resets_on_reload(logged_in_page: Page, houndarr_url: str) -> None:
    """The Admin collapsible always starts collapsed. Opening it holds
    within the session, but a reload (or fresh navigation) returns it to
    closed state and clears any stale ``houndarr.adminOpen`` value from a
    prior install that did persist the choice.

    Locks in the behaviour introduced in commit fdeeb74: persistence was
    removed because restoring "open" on every navigation hid the
    Instances table the Settings page is supposed to lead with.
    """
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    panel = page.locator("#admin-grouped")
    toggle = page.locator("#admin-toggle")
    # Fresh load is always closed.
    expect(panel).to_have_attribute("data-open", "false")
    toggle.click()
    page.wait_for_timeout(400)
    expect(panel).to_have_attribute("data-open", "true")
    # Reload: panel should return to closed regardless of prior state.
    page.reload()
    expect(page.locator("#admin-grouped")).to_have_attribute("data-open", "false")
    # And the legacy persistence key must have been cleared by init.
    stored = page.evaluate("() => localStorage.getItem('houndarr.adminOpen')")
    assert stored is None


def test_admin_security_confirm_password_match_indicator(
    logged_in_page: Page, houndarr_url: str
) -> None:
    """Typing a matching confirm-password paints the is-match indicator."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.locator("#new-password").fill("AnotherGood2!")
    page.locator("#confirm-password").fill("AnotherGood2!")
    expect(page.locator(".pw-match")).to_have_class(re.compile(r"is-match"))
    # A mismatch flips it to is-mismatch.
    page.locator("#confirm-password").fill("Different2!")
    expect(page.locator(".pw-match")).to_have_class(re.compile(r"is-mismatch"))


def test_admin_whats_new_button_opens_modal(logged_in_page: Page, houndarr_url: str) -> None:
    """The 'What's new' button force-opens the What's new modal."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.get_by_role("button", name=re.compile(r"what'?s\s*new", re.I)).click()
    expect(page.locator("dialog#changelog-modal[open]")).to_be_visible(timeout=4_000)


def test_admin_clear_logs_flash(logged_in_page: Page, houndarr_url: str) -> None:
    """Clear logs surfaces a success flash; the dialog closes automatically."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.locator('button[data-confirm-reset="logs"]').click()
    expect(page.locator("#confirm-dialog")).not_to_have_class(re.compile(r"hidden"))
    page.locator("#confirm-go").click()
    # Dialog closes and a toast lands in the flash slot.
    expect(page.locator("#confirm-dialog")).to_have_class(re.compile(r"hidden"), timeout=4_000)
    flash = page.locator("#admin-flash")
    expect(flash).to_contain_text(re.compile(r"Cleared|already empty", re.I), timeout=4_000)


def test_admin_reset_instances_empty_state_flash(logged_in_page: Page, houndarr_url: str) -> None:
    """With no instances configured the reset button renders the 'nothing to reset' flash."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.locator('button[data-confirm-reset="instances"]').click()
    expect(page.locator("#confirm-title")).to_contain_text("Reset policy settings")
    page.locator("#confirm-go").click()
    expect(page.locator("#admin-flash")).to_contain_text(
        re.compile(r"No instances configured|Policy settings reset", re.I),
        timeout=4_000,
    )


def test_admin_factory_reset_phrase_gates_submit(logged_in_page: Page, houndarr_url: str) -> None:
    """Confirm button stays disabled until the typed phrase matches RESET."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.locator('button[data-confirm-reset="factory"]').click()
    confirm_go = page.locator("#confirm-go")
    expect(confirm_go).to_be_disabled()
    page.locator("#confirm-phrase-input").fill("nope")
    expect(confirm_go).to_be_disabled()
    page.locator("#confirm-phrase-input").fill("RESET")
    expect(confirm_go).to_be_enabled()
    # Dismiss without submitting.  The backdrop is sized to the dialog
    # panel so the password input inside the panel intercepts a real
    # cursor click; dispatch the event directly so the test does not
    # depend on which child element a hit-test happens to land on.
    page.locator("[data-dismiss-confirm]").first.dispatch_event("click")
    expect(page.locator("#confirm-dialog")).to_have_class(re.compile(r"hidden"))


def test_admin_factory_reset_wrong_password_flash(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """Factory reset with wrong password renders a 422 error flash."""
    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.locator('button[data-confirm-reset="factory"]').click()
    page.locator("#confirm-phrase-input").fill("RESET")
    page.locator("#confirm-password-input").fill("WrongPassword123!")
    # The Factory reset button lives below the fold inside the dialog on
    # the headless browser viewports we run, and Playwright keeps marking
    # it "outside of the viewport" even after scroll_into_view_if_needed.
    # Submit the form via requestSubmit() so HTMX still picks up the
    # native submit event without a click hit-test.
    with page.expect_response(
        lambda r: "/settings/admin/factory-reset" in r.url and r.request.method == "POST"
    ) as resp_info:
        page.locator("#confirm-form").evaluate("f => f.requestSubmit()")
    assert resp_info.value.status == 422, resp_info.value.status
    expect(page.locator("#admin-flash")).to_contain_text(
        re.compile(r"password is incorrect", re.I),
        timeout=4_000,
    )


# ---------------------------------------------------------------------------
# Additional browser coverage added in the post-audit hardening pass.
# These guard behaviours that TestClient cannot observe: the browser-only
# HX-Refresh reload, the Caps-Lock modifier wiring, live instance CRUD
# without layout shift, and the preferences-switch rollback on 5xx.
# ---------------------------------------------------------------------------


_EXPECTED_500_CONSOLE_NOISE = [
    r"Failed to load resource: the server responded with a status of 500",
    r"Response Status Error Code 500",
]


def test_password_change_hx_refresh_recovers_csrf(logged_in_page: Page, houndarr_url: str) -> None:
    """After a successful password change the server sets HX-Refresh so the
    tab reloads and re-stamps hx-headers with the rotated CSRF cookie.
    Without that reload, the next mutating HTMX request would 403 because
    app.js captured the old token at initial page load. This test proves
    recovery by issuing another HTMX mutation (clear-logs) after the
    change and asserting its success flash lands.

    The password is changed to a temp value and restored inside a try /
    finally so a failure mid-test doesn't lock subsequent tests out of
    the shared container.
    """
    page = logged_in_page
    original_password = "CITestPass1!"
    temp_password = "TempCI999!"

    def _submit_password_change(current: str, new: str) -> tuple[int, dict[str, str]]:
        page.goto(f"{houndarr_url}/settings")
        section = page.locator("#admin-security")
        expect(section).to_be_visible()
        section.locator('input[name="current_password"]').fill(current)
        section.locator('input[name="new_password"]').fill(new)
        section.locator('input[name="new_password_confirm"]').fill(new)
        form = section.locator('form[hx-post="/settings/account/password"]')
        with page.expect_response(
            lambda r: "/settings/account/password" in r.url and r.request.method == "POST"
        ) as resp_info:
            form.evaluate("f => f.requestSubmit()")
        resp = resp_info.value
        return resp.status, dict(resp.headers)

    try:
        status, headers = _submit_password_change(original_password, temp_password)
        assert status == 200, f"password change returned {status}"
        assert headers.get("hx-refresh") == "true", (
            "server must set HX-Refresh: true so the tab reloads and the "
            "hx-headers attribute picks up the rotated CSRF cookie"
        )
        # The browser's HTMX runtime reads HX-Refresh and calls location.reload();
        # wait for the network to settle before asserting the recovery.
        page.wait_for_load_state("networkidle", timeout=10_000)

        # Prove the recovery: issue another HTMX mutation from the same tab.
        # Clear-logs is a low-risk POST that surfaces a flash on success.
        # If HX-Refresh didn't trigger (or app.js didn't re-stamp), this
        # request would hit the old CSRF token against the new session and
        # return 403 with no flash.
        page.goto(f"{houndarr_url}/settings")
        _open_admin_dropdown(page)
        page.locator('button[data-confirm-reset="logs"]').click()
        expect(page.locator("#confirm-dialog")).not_to_have_class(re.compile(r"hidden"))
        # Submit via the form's requestSubmit(); the dialog scroll region
        # often keeps Playwright thinking #confirm-go is offscreen even
        # after scroll_into_view_if_needed.  HTMX still receives the
        # native submit event and the rotated CSRF cookie from the
        # post-password-change reload.
        with page.expect_response(
            lambda r: "/settings/admin/clear-logs" in r.url and r.request.method == "POST"
        ) as clear_resp:
            page.locator("#confirm-form").evaluate("f => f.requestSubmit()")
        assert clear_resp.value.status == 200, (
            f"post-rotation clear-logs returned {clear_resp.value.status}; "
            "HX-Refresh recovery failed; hx-headers is still carrying the "
            "pre-rotation CSRF token"
        )
        expect(page.locator("#admin-flash")).to_contain_text(
            re.compile(r"Cleared|already empty", re.I),
            timeout=4_000,
        )
    finally:
        # Restore the original password so downstream tests using the
        # shared logged_in_page fixture keep working. Best-effort: if the
        # current password is already the original (test failed before
        # the first change), the 422 path is harmless.
        with suppress(Exception):
            _submit_password_change(temp_password, original_password)


def test_caps_lock_badge_toggles_with_modifier_state(
    logged_in_page: Page, houndarr_url: str
) -> None:
    """The Caps-Lock badge on password inputs toggles via
    ``event.getModifierState('CapsLock')`` in auth.js. Physical CapsLock
    state is a moving target across chromium / firefox / webkit headless
    runs, so this dispatches a synthetic keydown event with the modifier
    forced true (and again false) and asserts the class flip on the
    scoped .caps-badge. That exercises the exact wiring in auth.js
    without depending on OS-level modifier state behaviour.
    """
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    section = page.locator("#admin-security")
    expect(section).to_be_visible()

    # The badge should start hidden (no modifier active).
    current_field = section.locator("#current-password")
    current_field.click()
    caps_badge = section.locator("#current-password").locator(
        "xpath=ancestor::*[contains(@class,'field')][1]//*[contains(@class,'caps-badge')]"
    )

    # Simulate CapsLock-on keydown.
    page.evaluate(
        """() => {
          const input = document.getElementById('current-password');
          input.focus();
          const evt = new KeyboardEvent('keydown', { key: 'A', bubbles: true });
          Object.defineProperty(evt, 'getModifierState', {
            value: (k) => k === 'CapsLock',
          });
          input.dispatchEvent(evt);
        }"""
    )
    expect(caps_badge).to_have_class(re.compile(r"\bis-on\b"), timeout=2_000)

    # Simulate CapsLock-off keyup.
    page.evaluate(
        """() => {
          const input = document.getElementById('current-password');
          const evt = new KeyboardEvent('keyup', { key: 'A', bubbles: true });
          Object.defineProperty(evt, 'getModifierState', {
            value: () => false,
          });
          input.dispatchEvent(evt);
        }"""
    )
    expect(caps_badge).not_to_have_class(re.compile(r"\bis-on\b"), timeout=2_000)


def test_instance_toggle_and_delete_keeps_layout_stable(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """Add an instance, toggle enabled twice, verify the status cell and
    toggle-button widths do not shift under the Active <-> Disabled
    label swap, then delete the row. Locks in the row-stability fix at
    partials/instance_row.html:25-45 (min-w-[4.5rem] on the status pill
    and min-w-[4.25rem] on the toggle button) so a future padding edit
    cannot silently regress the flicker the user previously flagged.
    """
    page = logged_in_page
    instance_name = f"E2E Toggle {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")

    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    expect(add_form).to_be_visible()
    add_form.locator('input[name="name"]').fill(instance_name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-toggle-key")
    _wait_for_connection_ui_idle(page)
    _test_connection_and_wait_for_success(
        page, add_form, add_form.locator("button[data-test-connection-btn]")
    )
    _submit_form(add_form)
    expect(page.locator("#instance-tbody")).to_contain_text(instance_name, timeout=10_000)

    row = page.locator("#instance-tbody tr").filter(has_text=instance_name)
    status_cell = row.locator('[data-col="status"]')
    toggle_btn = row.locator('button[hx-post*="/toggle-enabled"]')

    # Capture initial geometry.
    initial_status_box = status_cell.bounding_box()
    initial_toggle_box = toggle_btn.bounding_box()
    assert initial_status_box is not None
    assert initial_toggle_box is not None

    # Toggle Active -> Disabled. Wait for the row to swap and the text to change.
    toggle_btn.click()
    expect(toggle_btn).to_have_text(re.compile(r"Enable", re.I), timeout=5_000)
    _wait_for_htmx_idle(page)

    after_status_box = status_cell.bounding_box()
    after_toggle_box = toggle_btn.bounding_box()
    assert after_status_box is not None
    assert after_toggle_box is not None
    # Allow 2 px of subpixel variance; anything more signals a reflow.
    assert abs(after_status_box["width"] - initial_status_box["width"]) <= 2, (
        f"status cell width shifted on toggle: "
        f"{initial_status_box['width']} -> {after_status_box['width']}"
    )
    assert abs(after_toggle_box["width"] - initial_toggle_box["width"]) <= 2, (
        f"toggle button width shifted on toggle: "
        f"{initial_toggle_box['width']} -> {after_toggle_box['width']}"
    )

    # Toggle back so the row is Active again, then delete.
    toggle_btn.click()
    expect(toggle_btn).to_have_text(re.compile(r"Disable", re.I), timeout=5_000)

    delete_btn = row.locator("button[hx-delete]")
    # hx-confirm triggers a window.confirm(); auto-accept it.
    page.once("dialog", lambda dialog: dialog.accept())
    delete_btn.click()
    expect(row).to_have_count(0, timeout=5_000)


def test_changelog_preferences_switch_rolls_back_on_error(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """W4 regression guard: the changelog-popup switch flips visually the
    instant the user clicks the checkbox, well before the form POST
    reaches the server. When /settings/changelog/preferences returns a
    5xx, settings_content.html's htmx:responseError handler must flip
    the checkbox back so the rendered state matches what actually
    persisted server-side. Intercept the endpoint with page.route to
    force a 500 and assert the rollback.
    """
    for p in _EXPECTED_500_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")

    # Make sure the Admin dropdown is open so #admin-updates is in layout.
    if page.locator("#admin-grouped").get_attribute("data-open") != "true":
        page.locator("#admin-toggle").click()
    expect(page.locator("#admin-updates")).to_be_visible()

    # Two `name="enabled"` checkboxes live under #admin-updates after the
    # changelog-popup toggle landed (auto-update-enable + changelog-popups).
    # Anchor on the wrapping form's hx-post so this test only ever drives
    # the changelog form whose /preferences endpoint we mock to 500 below.
    checkbox = page.locator(
        'form[hx-post="/settings/changelog/preferences"] input[type="checkbox"][name="enabled"]'
    )
    initial_checked = checkbox.is_checked()

    # Force every /preferences call during this test to fail with 500.
    pattern = re.compile(r"/settings/changelog/preferences$")
    page.route(pattern, lambda route: route.fulfill(status=500, body=""))

    try:
        with page.expect_response(
            lambda r: "/settings/changelog/preferences" in r.url and r.request.method == "POST"
        ) as resp_info:
            # The visible switch wraps the <input> in <span class="switch__track">
            # / <span class="switch__thumb"> overlays that intercept pointer
            # events.  A real-user click lands on the label; dispatch_event
            # bypasses the visual chrome so the test does not depend on which
            # span the cursor happens to hit.
            checkbox.dispatch_event("click")
        assert resp_info.value.status == 500

        # The htmx:responseError handler flips the checkbox back; give it a
        # moment to run and then assert the state matches the initial value.
        page.wait_for_timeout(200)
        assert checkbox.is_checked() == initial_checked, (
            "switch should roll back to its persisted state when the server rejects /preferences"
        )
    finally:
        page.unroute(pattern)

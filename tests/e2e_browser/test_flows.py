"""Browser end-to-end flows driven by pytest-playwright.

Parametrised by the ``--browser`` flag; the workflow runs chromium,
firefox, and webkit as separate matrix jobs.  Console errors and page
errors are caught by an autouse fixture in ``conftest.py``.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import Locator, Page, expect


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

    The ``#admin-grouped`` panel starts at ``data-open="false"`` on every
    fresh page load (``#admin-body`` has ``height:0; opacity:0`` until the
    inline JS in settings_content.html toggles it).  Tests that click into
    a control nested inside the dropdown need to open it first; otherwise
    Playwright waits 30s for the zero-height button to become actionable.
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
    # triggers the 422 guard path at routes/settings.py:594.
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


def test_admin_dropdown_toggle_persists(logged_in_page: Page, houndarr_url: str) -> None:
    """The Admin collapsible is closed by default; opening it persists via
    localStorage so a reload restores the user's last choice."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    panel = page.locator("#admin-grouped")
    toggle = page.locator("#admin-toggle")
    # Fresh load with no stored preference should leave the panel closed.
    expect(panel).to_have_attribute("data-open", "false")
    toggle.click()
    page.wait_for_timeout(400)
    expect(panel).to_have_attribute("data-open", "true")
    # Reload and confirm the opened preference persisted.
    page.reload()
    expect(page.locator("#admin-grouped")).to_have_attribute("data-open", "true")
    # Clean up: collapse again so the cleared localStorage matches default.
    page.locator("#admin-toggle").click()
    page.wait_for_timeout(400)
    page.evaluate("() => localStorage.removeItem('houndarr.adminOpen')")


@pytest.mark.skip(
    reason="Password-match indicator depends on static/js/auth.js which lands with PR24."
)
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


def test_admin_show_last_changelog_opens_modal(logged_in_page: Page, houndarr_url: str) -> None:
    """The 'Show last changelog' button force-opens the What's new modal."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.get_by_role("button", name=re.compile(r"show\s*last\s*changelog", re.I)).click()
    expect(page.locator("dialog#changelog-modal[open]")).to_be_visible(timeout=4_000)


def test_admin_view_full_changelog_navigates(logged_in_page: Page, houndarr_url: str) -> None:
    """The 'View full CHANGELOG.md' link navigates to /settings/changelog/full."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.get_by_role("link", name=re.compile(r"view\s*full\s*CHANGELOG", re.I)).click()
    expect(page).to_have_url(re.compile(r"/settings/changelog/full$"))
    expect(page.locator("[data-page-key='changelog-full']")).to_be_visible()


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
    # Dismiss without submitting. Target the Cancel button explicitly;
    # the backdrop also carries data-dismiss-confirm but its bounding-box
    # centre is occluded by the panel in grid-centred layouts, so
    # .first.click() lands on the panel and gets intercepted.
    page.locator("button[data-dismiss-confirm]").click()
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
    with page.expect_response(
        lambda r: "/settings/admin/factory-reset" in r.url and r.request.method == "POST"
    ) as resp_info:
        page.locator("#confirm-go").click()
    assert resp_info.value.status == 422, resp_info.value.status
    expect(page.locator("#admin-flash")).to_contain_text(
        re.compile(r"password is incorrect", re.I),
        timeout=4_000,
    )

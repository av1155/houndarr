"""Browser end-to-end flows driven by pytest-playwright.

Parametrised by the ``--browser`` flag; the workflow runs chromium,
firefox, and webkit as separate matrix jobs.  Console errors and page
errors are caught by an autouse fixture in ``conftest.py``.
"""

from __future__ import annotations

import re

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
    """
    with page.expect_response(
        lambda r: "/settings/instances/test-connection" in r.url and r.status == 200
    ):
        button.dispatch_event("click")
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


def test_login_redirects_to_dashboard(page: Page, houndarr_url: str) -> None:
    page.goto(houndarr_url)
    expect(page).to_have_url(re.compile(r"/login$"))

    page.get_by_role("textbox", name="Username").fill("admin")
    page.get_by_role("textbox", name="Password").fill("CITestPass1!")
    page.get_by_role("button", name="Sign In").click()

    expect(page).to_have_title("Dashboard | Houndarr")


def test_settings_page_renders(logged_in_page: Page, houndarr_url: str) -> None:
    logged_in_page.goto(f"{houndarr_url}/settings")
    expect(logged_in_page).to_have_title("Settings | Houndarr")


def test_add_form_preselects_random(logged_in_page: Page, houndarr_url: str) -> None:
    """The blank add-instance form renders Random as the selected option.

    Regression guard for the pass-1 HIGH finding on the random-search PR.
    Checked via the API route so it catches both the rendered HTML and
    the ``_blank_instance()`` factory state.
    """
    resp = logged_in_page.request.get(f"{houndarr_url}/settings/instances/add-form")
    assert resp.ok, resp.status
    body = resp.text()
    assert re.search(r'<option\s+value="random"[^>]*\sselected', body), body
    assert not re.search(r'<option\s+value="chronological"[^>]*\sselected', body)


def test_full_instance_lifecycle(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """Add an instance against the mock *arr, then flip search_order and verify persistence.

    Combines add and edit into one linear user story so the test does
    not depend on any sibling test's state.
    """
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")

    # Open the add modal.
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    expect(add_form).to_be_visible()

    # Fill in the form against the mock Sonarr.
    add_form.locator('input[name="name"]').fill("E2E Sonarr")
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
    expect(page.locator("#instance-tbody")).to_contain_text("E2E Sonarr", timeout=10_000)

    # Re-open to verify the default persisted as Random.
    page.locator('#instance-tbody button[hx-get^="/settings/instances/"]').first.click()
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

    # Re-open and verify persistence.
    page.locator('#instance-tbody button[hx-get^="/settings/instances/"]').first.click()
    reopened = page.locator('form[data-form-mode="edit"]')
    expect(reopened).to_be_visible()
    expect(reopened.locator('select[name="search_order"]')).to_have_value("chronological")

"""Browser end-to-end flows driven by pytest-playwright.

Parametrised by the ``--browser`` flag; the workflow runs chromium,
firefox, and webkit as separate matrix jobs.  Console errors and page
errors are caught by an autouse fixture in ``conftest.py``.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


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

    # Test Connection must succeed before Save is enabled.
    add_form.locator("button[data-test-connection-btn]").click()
    expect(page.locator("#instance-connection-status")).to_contain_text(
        "Connected to Sonarr",
        timeout=10_000,
    )

    # Save.
    add_form.locator("#instance-submit-btn").click()
    expect(page.locator("#instance-tbody")).to_contain_text("E2E Sonarr", timeout=10_000)

    # Re-open to verify the default persisted as Random.
    page.locator('#instance-tbody button[hx-get^="/settings/instances/"]').first.click()
    edit_form = page.locator('form[data-form-mode="edit"]')
    expect(edit_form).to_be_visible()
    expect(edit_form.locator('select[name="search_order"]')).to_have_value("random")

    # Flip to Chronological.  The edit form always starts with
    # connection_verified=false, so re-run the connection test first.
    edit_form.locator('select[name="search_order"]').select_option("chronological")
    edit_form.locator("button[data-test-connection-btn]").click()
    expect(page.locator("#instance-connection-status")).to_contain_text(
        "Connected to Sonarr",
        timeout=10_000,
    )
    edit_form.locator("#instance-submit-btn").click()
    expect(edit_form).to_be_hidden(timeout=10_000)

    # Re-open and verify persistence.
    page.locator('#instance-tbody button[hx-get^="/settings/instances/"]').first.click()
    reopened = page.locator('form[data-form-mode="edit"]')
    expect(reopened).to_be_visible()
    expect(reopened.locator('select[name="search_order"]')).to_have_value("chronological")

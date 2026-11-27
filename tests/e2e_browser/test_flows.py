"""Browser end-to-end flows driven by pytest-playwright.

Parametrised by the ``--browser`` flag; the workflow runs chromium,
firefox, and webkit as separate matrix jobs.  Console errors and page
errors are caught by an autouse fixture in ``conftest.py``.
"""

from __future__ import annotations

import re
import uuid
from contextlib import suppress

import pytest
from playwright.sync_api import Locator, Page, Route, expect

from houndarr.config import DEFAULT_BATCH_SIZE, DEFAULT_SLEEP_INTERVAL_MINUTES


def _wait_for_connection_ui_idle(page: Page) -> None:
    """Wait until ``#instance-connection-status`` has no status update pending.

    An edit to the type, URL or API key adds ``is-updating`` and queues the
    "Test connection before ..." prompt on an 80 ms timer; a server response
    that lands first cancels it.  Asserting the status text after this wait
    proves no queued prompt is left to overwrite it.
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


def _expect_dialog_ready(page: Page, form: Locator) -> None:
    """Wait for the instance dialog's form and for htmx to wire its controls.

    htmx 2 processes swapped-in content in its settle step, 20 ms after
    the dialog opens, so a click before that sends no request.
    """
    expect(form).to_be_visible()
    _wait_for_htmx_idle(page)


def _test_connection_and_wait_for_success(page: Page, form: Locator, button: Locator) -> None:
    """Click Test Connection the way a user does and wait for the success state.

    The click blurs the field filled last, and the response from the mock
    on the same Docker network often lands inside the 80 ms that field's
    change event queues its prompt for, so "Connected" next to an enabled
    Save proves the response won (#931).

    The response filter matches any status for the test-connection URL.
    Filtering on ``status == 200`` would hang for the full Playwright
    timeout on a 422 (connection failure) and surface as an opaque
    wait-timeout instead of a legible status code.
    """
    with page.expect_response(
        lambda r: "/settings/instances/test-connection" in r.url
    ) as resp_info:
        button.click()
    resp = resp_info.value
    assert resp.status == 200, f"test-connection returned {resp.status}: {resp.text()}"
    _wait_for_connection_ui_idle(page)
    _wait_for_htmx_idle(page)
    expect(page.locator("#instance-connection-status")).to_contain_text("Connected to")
    expect(form.locator("#instance-submit-btn")).to_be_enabled(timeout=10_000)


def _expect_blocked_url_error_to_stay(page: Page, form: Locator) -> None:
    """Type a loopback URL and click Test Connection at once; the server's error must stay.

    The click blurs the URL field, and the 422 for a blocked address comes
    back faster than the prompt its change event queues (#931).
    """
    form.locator('input[name="url"]').fill("http://127.0.0.1")
    with page.expect_response(
        lambda r: "/settings/instances/test-connection" in r.url
    ) as resp_info:
        form.locator("button[data-test-connection-btn]").click()
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_connection_ui_idle(page)
    _wait_for_htmx_idle(page)
    expect(page.locator("#instance-connection-status")).to_contain_text("blocked address range")
    expect(form.locator("#instance-submit-btn")).to_be_disabled()


def _open_filled_add_form(page: Page, mock_sonarr_url: str, name: str) -> Locator:
    """Open the Add Instance dialog and fill a Sonarr instance against the mock."""
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    _expect_dialog_ready(page, add_form)
    add_form.locator('input[name="name"]').fill(name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-sonarr-key")
    return add_form


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
    """Click the Admin toggle and wait for the panel to stop moving.

    Commit fdeeb74 made ``#admin-body`` start at ``height:0; opacity:0``
    on every page load, so any test that clicks into an element nested
    inside the dropdown needs to open it first. Playwright would
    otherwise wait 30s for a zero-height element to become actionable
    and fail with ``pointer events intercepted``.

    ``data-open`` is not a settled signal on its own. ``setOpen`` in
    static/js/settings.js flips it at the *start* of the 260ms expand,
    then animates the height, so releasing on the attribute hands back
    control while the controls at the bottom of the panel are still
    travelling. Playwright decides a target is stable by comparing its
    box across two frames, and late in an eased transition the per-frame
    delta is small enough for that to pass, so the click dispatches
    against coordinates that go stale before the event lands. It reports
    success and the page handler never fires.

    Both branches of ``setOpen`` finish by writing an inline
    ``height: auto``: the animated one in its end-of-transition timer,
    the prefers-reduced-motion one immediately. That makes the inline
    height the one observable meaning "geometry is final" in either
    mode. Read the CSSOM property rather than the computed style, which
    resolves ``auto`` to pixels and so can never match.
    """
    panel = page.locator("#admin-grouped")
    if panel.get_attribute("data-open") != "true":
        page.locator("#admin-toggle").click()
    # Keep asserting the attribute: it is what proves the toggle click
    # registered at all rather than being swallowed by setOpen's
    # re-entrancy guard, and it fails with a clearer message than the
    # settle wait below would.
    expect(panel).to_have_attribute("data-open", "true")
    page.wait_for_function(
        "() => document.getElementById('admin-body')?.style.height === 'auto'",
        timeout=5_000,
    )


# Every type-gated control in the instance form, with the types that show it.
_TYPE_GATED_CONTROLS: tuple[tuple[str, frozenset[str]], ...] = (
    ('p:has-text("Sonarr-based, focuses on studios.")', frozenset({"whisparr_v2"})),
    ('p:has-text("Radarr-based, focuses on scenes.")', frozenset({"whisparr_v3"})),
    ('[name="sonarr_search_mode"]', frozenset({"sonarr"})),
    ('[name="lidarr_search_mode"]', frozenset({"lidarr"})),
    ('[name="readarr_search_mode"]', frozenset({"readarr"})),
    ('[name="whisparr_v2_search_mode"]', frozenset({"whisparr_v2"})),
    ('[name="upgrade_series_window_size"]', frozenset({"sonarr", "whisparr_v2"})),
    ('[name="upgrade_sonarr_search_mode"]', frozenset({"sonarr"})),
    ('[name="upgrade_lidarr_search_mode"]', frozenset({"lidarr"})),
    ('[name="upgrade_readarr_search_mode"]', frozenset({"readarr"})),
    ('[name="upgrade_whisparr_v2_search_mode"]', frozenset({"whisparr_v2"})),
)


def _assert_type_gated_controls(form: Locator, instance_type: str) -> None:
    """Assert each type-gated control is shown exactly for the types that use it."""
    for selector, types in _TYPE_GATED_CONTROLS:
        control = form.locator(selector)
        expect(control).to_have_count(1)
        if instance_type in types:
            expect(control, f"{selector} hidden for {instance_type}").to_be_visible()
        else:
            expect(control, f"{selector} shown for {instance_type}").to_be_hidden()


def _assert_hiding_restores_series_window(form: Locator, rendered_value: str) -> None:
    """Switch a Sonarr form with an out-of-range window to Radarr; no hidden field may be invalid.

    The browser cannot point at a hidden invalid field, so it would block Save silently.
    """
    window = form.locator('[name="upgrade_series_window_size"]')
    window.fill("0")
    expect(window).to_have_value("0")
    form.locator('select[name="type"]').select_option("radarr")
    expect(window).to_be_hidden()
    expect(window).to_have_value(rendered_value)
    hidden_invalid = form.evaluate(
        "f => [...f.elements]"
        ".filter((el) => el.willValidate && !el.validity.valid && !el.checkVisibility())"
        ".map((el) => el.name)"
    )
    assert hidden_invalid == [], f"hidden fields fail validation: {hidden_invalid}"


@pytest.mark.integration
def test_full_instance_lifecycle(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str, console_guard
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
    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    instance_name = f"E2E Sonarr {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")

    # Open the add modal.
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    _expect_dialog_ready(page, add_form)

    # Fill in the form against the mock Sonarr.
    add_form.locator('input[name="name"]').fill(instance_name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-sonarr-key")

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
    _expect_dialog_ready(page, edit_form)
    expect(edit_form.locator('select[name="search_order"]')).to_have_value("random")

    # Flip to Chronological.  The edit form always starts with
    # connection_verified=false, so re-run the connection test first.
    edit_form.locator('select[name="search_order"]').select_option("chronological")
    # Not the default 5, so the hide check below can tell the stored value from the default.
    edit_form.locator('[name="upgrade_series_window_size"]').fill("37")
    _expect_blocked_url_error_to_stay(page, edit_form)
    edit_form.locator('input[name="url"]').fill(mock_sonarr_url)
    _test_connection_and_wait_for_success(
        page, edit_form, edit_form.locator("button[data-test-connection-btn]")
    )
    _submit_form(edit_form)
    expect(edit_form).to_be_hidden(timeout=10_000)

    # Re-open the same row and verify persistence.
    row.locator('button[hx-get^="/settings/instances/"]').click()
    reopened = page.locator('form[data-form-mode="edit"]')
    _expect_dialog_ready(page, reopened)
    expect(reopened.locator('select[name="search_order"]')).to_have_value("chronological")
    expect(reopened.locator('[name="upgrade_series_window_size"]')).to_have_value("37")

    # The Edit form's Type select stays editable, so its gated controls follow it too.
    _assert_type_gated_controls(reopened, "sonarr")
    _assert_hiding_restores_series_window(reopened, "37")
    _assert_type_gated_controls(reopened, "radarr")
    reopened.locator('select[name="type"]').select_option("whisparr_v2")
    _assert_type_gated_controls(reopened, "whisparr_v2")


@pytest.mark.integration
def test_add_form_type_gated_controls_follow_type(logged_in_page: Page, houndarr_url: str) -> None:
    """Each Type pick in the Add form shows that type's controls and hides the rest."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    _expect_dialog_ready(page, add_form)
    type_select = add_form.locator('select[name="type"]')
    expect(type_select).to_have_value("radarr")
    _assert_type_gated_controls(add_form, "radarr")

    for instance_type in (
        "sonarr",
        "radarr",
        "whisparr_v2",
        "lidarr",
        "readarr",
        "whisparr_v3",
        "sonarr",
    ):
        type_select.select_option(instance_type)
        _assert_type_gated_controls(add_form, instance_type)

    _assert_hiding_restores_series_window(add_form, "5")


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


@pytest.mark.integration
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
    _expect_dialog_ready(page, add_form)

    add_form.locator('input[name="name"]').fill(f"E2E Guard {uuid.uuid4().hex[:8]}")
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("guard-key")

    # requestSubmit with ``connection_verified=false`` in the hidden input
    # triggers the 422 guard path in routes/settings/instances.py
    # (instance_create, the connection_verified != "true" branch).
    with page.expect_response(
        lambda r: r.url.endswith("/settings/instances") and r.request.method == "POST"
    ) as resp_info:
        _submit_form(add_form)
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_connection_ui_idle(page)
    _wait_for_htmx_idle(page)

    expect(page.locator("#instance-connection-status")).to_contain_text(
        "Test connection successfully before adding.",
        timeout=5_000,
    )


@pytest.mark.integration
def test_number_typed_in_exponent_form_saves_its_value(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """``1e2`` is a whole number to a step-1 number input, which submits the text as typed.

    The server rejects exponent notation, so the form must send the parsed
    value; otherwise the save fails and the dialog closes with nothing saved.
    """
    page = logged_in_page
    instance_name = f"E2E Exponent {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    _expect_dialog_ready(page, add_form)

    add_form.locator('input[name="name"]').fill(instance_name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-exponent-key")
    add_form.locator('input[name="batch_size"]').fill("1e2")
    _test_connection_and_wait_for_success(
        page, add_form, add_form.locator("button[data-test-connection-btn]")
    )

    with page.expect_response(
        lambda r: r.url.endswith("/settings/instances") and r.request.method == "POST"
    ) as resp_info:
        _submit_form(add_form)
    assert resp_info.value.status == 200, resp_info.value.status
    expect(page.locator("#instance-tbody")).to_contain_text(instance_name, timeout=10_000)

    row = page.locator("#instance-tbody tr").filter(has_text=instance_name)
    row.locator('button[hx-get^="/settings/instances/"]').click()
    edit_form = page.locator('form[data-form-mode="edit"]')
    _expect_dialog_ready(page, edit_form)
    expect(edit_form.locator('input[name="batch_size"]')).to_have_value("100")


@pytest.mark.integration
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
    _expect_dialog_ready(page, add_form)

    add_form.locator('input[name="name"]').fill(f"E2E SSRF {uuid.uuid4().hex[:8]}")
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="api_key"]').fill("guard-key")
    _expect_blocked_url_error_to_stay(page, add_form)


@pytest.mark.integration
def test_connection_result_for_edited_details_is_dropped(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """A test result that lands after the URL was edited must not verify the new URL."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    add_form = _open_filled_add_form(page, mock_sonarr_url, f"E2E Stale {uuid.uuid4().hex[:8]}")
    status = page.locator("#instance-connection-status")
    prompt = "Test connection before adding this instance."

    held: list[Route] = []

    def hold(route: Route) -> None:
        held.append(route)

    pattern = re.compile(r"/settings/instances/test-connection$")
    page.route(pattern, hold)
    try:
        test_btn = add_form.locator("button[data-test-connection-btn]")
        test_btn.click()
        expect(test_btn).to_have_class(re.compile(r"\bis-testing\b"))
        add_form.locator('input[name="url"]').fill("http://10.255.255.1:8989")
        # The edit's prompt is on screen before the old result arrives.
        _wait_for_connection_ui_idle(page)
        expect(status).to_contain_text(prompt)
        assert len(held) == 1, f"expected one held test-connection request, got {len(held)}"

        with page.expect_response(
            lambda r: "/settings/instances/test-connection" in r.url
        ) as resp_info:
            held[0].continue_()
        assert resp_info.value.status == 200, resp_info.value.status
        _wait_for_htmx_idle(page)

        expect(status).to_contain_text(prompt)
        expect(status).not_to_contain_text("Connected")
        expect(add_form.locator("#instance-submit-btn")).to_be_disabled()
        expect(add_form.locator("#instance-connection-verified")).to_have_value("false")
    finally:
        page.unroute(pattern)


@pytest.mark.integration
def test_refused_setting_keeps_save_enabled(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str, console_guard
) -> None:
    """A save refused for a setting needs no new connection test once the setting is fixed."""
    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    instance_name = f"E2E Setting {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")
    add_form = _open_filled_add_form(page, mock_sonarr_url, instance_name)
    _test_connection_and_wait_for_success(
        page, add_form, add_form.locator("button[data-test-connection-btn]")
    )

    cooldown = add_form.locator('input[name="cooldown_days"]')
    save = add_form.locator("#instance-submit-btn")
    cooldown.fill("99999")
    with page.expect_response(
        lambda r: r.url.endswith("/settings/instances") and r.request.method == "POST"
    ) as resp_info:
        save.click()
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_htmx_idle(page)
    expect(page.locator("#instance-connection-status")).to_contain_text(
        "Cooldown days must be 3650 or less."
    )
    expect(save).to_be_enabled()
    expect(add_form.locator("#instance-connection-verified")).to_have_value("true")

    cooldown.fill("7")
    with page.expect_response(
        lambda r: r.url.endswith("/settings/instances") and r.request.method == "POST"
    ) as resp_info:
        save.click()
    assert resp_info.value.status == 200, resp_info.value.status
    expect(page.locator("#instance-tbody")).to_contain_text(instance_name, timeout=10_000)


@pytest.mark.integration
def test_run_now_clicked_again_stays_running_until_its_own_response(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """A second Run now during the Queued hold stays Running while its request is in flight.

    The first request's hold timer used to reset the button to an enabled
    Run now 900 ms after Queued, with the second request still running.
    """
    page = logged_in_page
    instance_name = f"E2E Run now {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")
    add_form = _open_filled_add_form(page, mock_sonarr_url, instance_name)
    _test_connection_and_wait_for_success(
        page, add_form, add_form.locator("button[data-test-connection-btn]")
    )
    _submit_form(add_form)
    expect(page.locator("#instance-tbody")).to_contain_text(instance_name, timeout=10_000)

    accepted = {"status": 202, "content_type": "application/json", "body": '{"status":"accepted"}'}
    calls: list[Route] = []

    def respond(route: Route) -> None:
        calls.append(route)
        if len(calls) == 1:
            route.fulfill(**accepted)

    pattern = re.compile(r"/api/instances/\d+/run-now$")
    page.route(pattern, respond)
    try:
        page.goto(f"{houndarr_url}/")
        card = page.locator(".dash-card").filter(
            has=page.locator(".dash-card__name", has_text=instance_name)
        )
        button = card.locator('[data-run-now-btn="true"]')
        label = button.locator("[data-run-now-label]")
        expect(button).to_be_enabled()
        instance_id = button.get_attribute("data-instance-id")

        button.click()
        # Polls every frame: the Queued hold is the only window a second click can land in.
        page.wait_for_function(
            """id => document.querySelector(
                `[data-run-now-btn][data-instance-id="${id}"] [data-run-now-label]`
            )?.textContent === 'Queued'""",
            arg=instance_id,
        )
        button.click()
        expect(label).to_have_text("Running…")

        page.wait_for_timeout(1_500)
        expect(label).to_have_text("Running…")
        expect(button).to_be_disabled()
        assert len(calls) == 2, f"expected two run-now requests, got {len(calls)}"

        calls[1].fulfill(**accepted)
        expect(label).to_have_text("Queued")
        expect(label).to_have_text("Run now")
        expect(button).to_be_enabled()
    finally:
        for route in calls[1:]:
            with suppress(Exception):
                route.fulfill(**accepted)
        page.unroute(pattern)


@pytest.mark.integration
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


@pytest.mark.integration
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


@pytest.mark.integration
def test_admin_security_confirm_password_match_indicator(
    logged_in_page: Page, houndarr_url: str
) -> None:
    """Typing a matching confirm-password paints the is-match indicator."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    page.locator("#new-password").fill("AnotherGood2!")
    page.locator("#confirm-password").fill("AnotherGood2!")
    expect(page.locator(".pw-match")).to_have_class(re.compile(r"is-match"))
    # A mismatch flips it to is-mismatch.
    page.locator("#confirm-password").fill("Different2!")
    expect(page.locator(".pw-match")).to_have_class(re.compile(r"is-mismatch"))


@pytest.mark.integration
def test_password_eye_toggle_works_after_422_swap(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """The eye toggle on every password input still flips type after a 422 swap.

    Regression for the case where ``hx-swap="outerHTML"`` replaced
    ``#admin-security`` and ``auth.js`` re-bound listeners against the
    detached pre-swap subtree, leaving the new visible buttons unwired.
    Submitting the form with the current admin password in every field
    triggers the same-password 422 branch; after that swap the toggle on
    each field must still flip ``input.type`` from ``password`` to ``text``.
    """
    from tests.e2e_browser.conftest import ADMIN_PASS

    for p in _EXPECTED_422_CONSOLE_NOISE:
        console_guard.allow(p)

    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    # ``#admin-security`` lives inside the collapsible ``#admin-grouped``
    # panel that ships closed on every fresh page load.  ``fill`` /
    # ``requestSubmit`` / text-assertions go through JS and work on a
    # collapsed subtree, but the toggle ``click()`` below issues a real
    # pointer event the closed panel intercepts.  Open the panel up
    # front so the click can land on the post-swap button.
    _open_admin_dropdown(page)
    section = page.locator("#admin-security")
    expect(section).to_be_visible()

    pw_form = section.locator('form[hx-post="/settings/account/password"]')
    pw_form.locator('input[name="current_password"]').fill(ADMIN_PASS)
    pw_form.locator('input[name="new_password"]').fill(ADMIN_PASS)
    pw_form.locator('input[name="new_password_confirm"]').fill(ADMIN_PASS)

    with page.expect_response(
        lambda r: "/settings/account/password" in r.url and r.request.method == "POST"
    ) as resp_info:
        pw_form.evaluate("f => f.requestSubmit()")
    assert resp_info.value.status == 422, resp_info.value.status
    _wait_for_htmx_idle(page)

    expect(page.locator("#admin-security")).to_contain_text(
        "New password must be different from current password.",
        timeout=5_000,
    )

    # After the swap, every password input in the form must respond to
    # its eye-toggle button by flipping its ``type`` attribute.  All three
    # fields share the same wiring path, so a regression in one would
    # imply a regression in the others.
    for input_id in ("current-password", "new-password", "confirm-password"):
        field = page.locator("#admin-security").locator(f"#{input_id}")
        expect(field).to_have_attribute("type", "password")
        toggle = field.locator(
            "xpath=ancestor::div[contains(@class, 'input-wrap')]//button[@data-pw-toggle]"
        )
        toggle.click()
        expect(field).to_have_attribute("type", "text", timeout=2_000)
        toggle.click()
        expect(field).to_have_attribute("type", "password", timeout=2_000)


@pytest.mark.integration
def test_caps_lock_badge_surfaces_on_focus_when_already_on(
    logged_in_page: Page, houndarr_url: str
) -> None:
    """Caps-lock badge appears on focus when CapsLock was already engaged.

    FocusEvent has no ``getModifierState``, so the badge handler cannot
    read live OS state at focus time.  ``auth.js`` keeps a module-scope
    cache fed by document-level keydown/keyup/mousedown listeners and
    re-applies that cache on focus.  This test seeds the cache with a
    synthetic keydown that reports CapsLock=true, focuses a password
    input, and asserts the badge picks it up without any in-field key
    press.
    """
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    section = page.locator("#admin-security")
    expect(section).to_be_visible()

    field = section.locator("#new-password")
    badge_locator = (
        "xpath=ancestor::div[contains(@class, 'field')]//span[contains(@class, 'caps-badge')]"
    )
    badge = field.locator(badge_locator)

    # Sanity: badge is NOT on before any caps event.
    expect(badge).not_to_have_class(re.compile(r"is-on"))

    # Seed the global tracker by dispatching a synthetic keydown whose
    # getModifierState reports CapsLock active.  KeyboardEvent's own
    # getModifierState is read-only, so override it on the dispatched
    # instance via Object.defineProperty.  The capture-phase document
    # listener inside auth.js consumes the event before any per-input
    # listener can interfere.
    page.evaluate(
        """() => {
            const ev = new KeyboardEvent('keydown', { bubbles: true });
            Object.defineProperty(ev, 'getModifierState', {
                value: (key) => key === 'CapsLock',
            });
            document.dispatchEvent(ev);
        }"""
    )

    field.focus()

    expect(badge).to_have_class(re.compile(r"is-on"), timeout=2_000)


@pytest.mark.integration
def test_admin_whats_new_button_opens_modal(logged_in_page: Page, houndarr_url: str) -> None:
    """The 'What's new' button force-opens the What's new modal."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.get_by_role("button", name=re.compile(r"what'?s\s*new", re.I)).click()
    expect(page.locator("dialog#changelog-modal[open]")).to_be_visible(timeout=4_000)


@pytest.mark.integration
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


@pytest.mark.integration
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


@pytest.mark.integration
def test_admin_factory_reset_phrase_gates_submit(logged_in_page: Page, houndarr_url: str) -> None:
    """Confirm button stays disabled until the typed phrase matches RESET."""
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    # Regression guard for the helper's contract. `setOpen` leaves the
    # inline height at `auto` only once the panel has finished opening,
    # so anything else here means the Factory reset button below is still
    # travelling and the click that follows can miss it.
    assert page.locator("#admin-body").evaluate("el => el.style.height") == "auto"
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


@pytest.mark.integration
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


@pytest.mark.integration
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
        _open_admin_dropdown(page)
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
        with page.expect_response(
            lambda r: "/settings/admin/clear-logs" in r.url and r.request.method == "POST"
        ) as clear_resp:
            page.locator("#confirm-go").click()
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


@pytest.mark.integration
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


@pytest.mark.integration
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
    _expect_dialog_ready(page, add_form)
    add_form.locator('input[name="name"]').fill(instance_name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-toggle-key")
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
    # The label flips mid-swap, so match the first toggle above and wait for
    # the swap to settle. Otherwise the delete click below can land on the
    # row HTMX is still replacing.
    _wait_for_htmx_idle(page)

    delete_btn = row.locator("button[hx-delete]")
    # settings.js intercepts htmx:confirm and routes delete through the
    # shared danger-tone dialog instead of window.confirm(); confirm via
    # the dialog's primary button.
    delete_btn.click()
    expect(page.locator("#confirm-dialog")).not_to_have_class(re.compile(r"hidden"))
    page.locator("#confirm-go").click()
    expect(row).to_have_count(0, timeout=5_000)


@pytest.mark.integration
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
    # `to_be_visible` alone is too weak a settle here: a child clipped by
    # the still-collapsing overflow-hidden panel still reports a box.
    _open_admin_dropdown(page)
    expect(page.locator("#admin-updates")).to_be_visible()

    # Commit 45e0fdd added a second input[name="enabled"] inside
    # #admin-updates for the release-auto-check toggle, so the old
    # #admin-updates input[type="checkbox"][name="enabled"] selector now
    # resolves to 2 elements and fails Playwright's strict mode. Scope
    # to the changelog form specifically.
    checkbox = page.locator('form[hx-post="/settings/changelog/preferences"] input[name="enabled"]')
    initial_checked = checkbox.is_checked()

    # The iOS-style switch layers a .switch__thumb span on top of the
    # native <input>, so Playwright's element-centre click lands on the
    # span and gets intercepted. Click the wrapping <label> so the
    # browser forwards the click to the input natively.
    toggle_label = page.locator('form[hx-post="/settings/changelog/preferences"] label').first

    # Force every /preferences call during this test to fail with 500.
    pattern = re.compile(r"/settings/changelog/preferences$")
    page.route(pattern, lambda route: route.fulfill(status=500, body=""))

    try:
        with page.expect_response(
            lambda r: "/settings/changelog/preferences" in r.url and r.request.method == "POST"
        ) as resp_info:
            toggle_label.click()
        assert resp_info.value.status == 500

        # The click flips the switch optimistically and the
        # htmx:responseError handler flips it back.  expect() retries the
        # read until the handler lands, so the rollback does not have to
        # finish inside a fixed wait.
        expect(checkbox).to_be_checked(checked=initial_checked)
    finally:
        page.unroute(pattern)


@pytest.mark.integration
def test_api_key_reveal_copy_fallback_targets_open_dialog(
    logged_in_page: Page, houndarr_url: str
) -> None:
    """The Copy key button writes the plaintext key in non-secure contexts.

    The reveal modal opens via ``<dialog>.showModal()``; content outside
    an open modal dialog is inert per the HTML spec, so a fallback
    textarea appended to ``document.body`` never receives focus or
    selection and ``execCommand('copy')`` silently succeeds over nothing
    while the toast misleadingly reports success.  The fallback host
    must therefore be the open dialog itself.  Instrument
    ``execCommand`` and assert the textarea sits inside the dialog and
    the selection covers the full key at call time.
    """
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)

    # Open the reveal dialog. Generate from a clean state, or regenerate
    # if a previous test in the session already configured a key.
    submit = page.locator('form[action="/settings/api-key/generate"] button[type="submit"]')
    if submit.inner_text().strip() == "Regenerate key":
        submit.click()
        page.locator("#confirm-go").click()
    else:
        submit.click()
    reveal = page.locator("dialog#api-key-reveal-modal[open]")
    expect(reveal).to_be_visible(timeout=4_000)

    # Force the non-secure-context fallback path by removing the
    # clipboard API, and instrument execCommand to capture the textarea
    # state at the moment the copy call fires.
    page.evaluate(
        """() => {
            Object.defineProperty(navigator, 'clipboard', {
                value: undefined,
                configurable: true,
            });
            window.__execCopyState = null;
            const orig = document.execCommand.bind(document);
            document.execCommand = function(cmd) {
                const ta =
                    document.querySelector('dialog[open] textarea')
                    || document.querySelector('body > textarea');
                window.__execCopyState = {
                    cmd,
                    selectionEnd: ta ? ta.selectionEnd : null,
                    valueLength: ta ? ta.value.length : null,
                    parentTag: ta ? ta.parentElement.tagName : null,
                };
                return orig(cmd);
            };
        }"""
    )

    page.get_by_role("button", name="Copy key").click()
    page.wait_for_function("() => window.__execCopyState !== null")
    state = page.evaluate("() => window.__execCopyState")

    assert state["cmd"] == "copy"
    assert state["valueLength"] is not None and state["valueLength"] > 0, (
        f"fallback textarea never received the key text: {state!r}"
    )
    assert state["selectionEnd"] == state["valueLength"], (
        f"selection covered {state['selectionEnd']} of "
        f"{state['valueLength']} chars; the fallback textarea was likely "
        "inert because it landed outside the open modal dialog"
    )
    assert state["parentTag"] == "DIALOG", (
        f"fallback textarea parent was {state['parentTag']!r}, expected DIALOG"
    )


@pytest.mark.integration
def test_admin_reset_instances_refreshes_table_without_reload(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str
) -> None:
    """After Reset all instance settings the table row shows the default batch / interval."""
    page = logged_in_page
    instance_name = f"E2E Reset {uuid.uuid4().hex[:8]}"
    page.goto(f"{houndarr_url}/settings")

    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    _expect_dialog_ready(page, add_form)
    add_form.locator('input[name="name"]').fill(instance_name)
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-sonarr-key")
    add_form.locator('input[name="batch_size"]').fill("7")
    add_form.locator('input[name="sleep_interval_mins"]').fill("45")
    _test_connection_and_wait_for_success(
        page, add_form, add_form.locator("button[data-test-connection-btn]")
    )
    _submit_form(add_form)
    batch_cell = (
        page.locator("#instance-tbody tr")
        .filter(has_text=instance_name)
        .locator('[data-col="batch"]')
    )
    expect(batch_cell).to_have_text(re.compile(r"^\s*7 / 45m\s*$"), timeout=10_000)

    page.evaluate("() => { window.__resetNoReload = true; }")
    _open_admin_dropdown(page)
    page.locator('button[data-confirm-reset="instances"]').click()
    page.locator("#confirm-go").click()

    expect(page.locator("#admin-flash")).to_contain_text("Policy settings reset", timeout=4_000)
    expect(batch_cell).to_have_text(
        re.compile(rf"^\s*{DEFAULT_BATCH_SIZE} / {DEFAULT_SLEEP_INTERVAL_MINUTES}m\s*$"),
        timeout=4_000,
    )
    assert page.evaluate("() => window.__resetNoReload === true")


@pytest.mark.integration
def test_admin_action_server_error_shows_toast(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """An unswapped 5xx on an admin action surfaces the global toast instead of nothing."""
    for p in _EXPECTED_500_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)

    pattern = re.compile(r"/settings/admin/clear-logs$")
    page.route(pattern, lambda route: route.fulfill(status=500, body="Internal Server Error"))
    try:
        page.locator('button[data-confirm-reset="logs"]').click()
        with page.expect_response(
            lambda r: "/settings/admin/clear-logs" in r.url and r.request.method == "POST"
        ):
            page.locator("#confirm-go").click()
        expect(page.locator("#toast")).to_be_visible(timeout=4_000)
        expect(page.locator("#toast")).to_contain_text("Request failed (HTTP 500)")
        expect(page.locator("#admin-flash")).not_to_contain_text("Internal Server Error")
    finally:
        page.unroute(pattern)


_EXPECTED_429_CONSOLE_NOISE = [
    r"Failed to load resource: the server responded with a status of 429",
    r"Response Status Error Code 429",
]


@pytest.mark.integration
def test_admin_factory_reset_rate_limit_flash_renders(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """The factory-reset 429 flash lands in #admin-flash.

    The response is stubbed: tripping the real limit would lock /login for
    the rest of the suite, since both share one IP-scoped bucket.
    """
    for p in _EXPECTED_429_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)

    flash = (
        '<div data-admin-flash data-admin-flash-tone="danger" role="status">'
        "Too many attempts. Try again in a minute.</div>"
    )
    pattern = re.compile(r"/settings/admin/factory-reset$")
    page.route(
        pattern,
        lambda route: route.fulfill(
            status=429,
            body=flash,
            content_type="text/html",
            headers={"X-Houndarr-Swap-Error": "true"},
        ),
    )
    try:
        page.locator('button[data-confirm-reset="factory"]').click()
        page.locator("#confirm-phrase-input").fill("RESET")
        page.locator("#confirm-password-input").fill("WrongPassword123!")
        page.locator("#confirm-go").click()
        expect(page.locator("#admin-flash")).to_contain_text("Too many attempts", timeout=4_000)
        expect(page.locator("#toast")).to_be_hidden()
    finally:
        page.unroute(pattern)


_TEST_CONNECTION_500 = "Request failed (HTTP 500)"


def _fail_test_connection_in_add_modal(page: Page, mock_sonarr_url: str) -> Locator:
    """Open the add modal, run Test Connection against a routed 500, return its toast."""
    page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
    add_form = page.locator('form[data-form-mode="add"]')
    _expect_dialog_ready(page, add_form)
    add_form.locator('input[name="name"]').fill(f"E2E Toast {uuid.uuid4().hex[:8]}")
    add_form.locator('select[name="type"]').select_option("sonarr")
    add_form.locator('input[name="url"]').fill(mock_sonarr_url)
    add_form.locator('input[name="api_key"]').fill("e2e-sonarr-key")
    with page.expect_response(lambda r: "/settings/instances/test-connection" in r.url):
        add_form.locator("button[data-test-connection-btn]").dispatch_event("click")
    toast = page.locator("#add-instance-modal > .toast")
    expect(toast).to_be_visible(timeout=4_000)
    expect(toast).to_contain_text(_TEST_CONNECTION_500)
    return toast


def _assert_toast_uncovered(toast: Locator) -> None:
    """``to_be_visible`` passes under a modal backdrop too; hit-test the toast's centre."""
    uncovered = toast.evaluate(
        """el => {
            const r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return el.contains(hit);
        }"""
    )
    assert uncovered, "the toast is drawn under the instance modal"


def _close_add_modal_and_expect_page_toast(page: Page) -> None:
    page.locator('#add-instance-modal [data-close-add-instance-modal="true"]').first.click()
    expect(page.locator("dialog#add-instance-modal[open]")).to_have_count(0, timeout=4_000)
    expect(page.locator("#toast")).to_be_visible()
    expect(page.locator("#toast")).to_contain_text(_TEST_CONNECTION_500)


@pytest.mark.integration
def test_error_toast_draws_above_open_instance_modal(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str, console_guard
) -> None:
    """A failed request from the instance modal shows a toast the modal does not cover.

    The modal is a top-layer <dialog>, so a toast left on <body> would sit
    under its backdrop.  Closing the modal hands the message to the page
    toast, and a reopened modal must draw its next toast on top again.
    """
    for p in _EXPECTED_500_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    pattern = re.compile(r"/settings/instances/test-connection$")
    page.route(pattern, lambda route: route.fulfill(status=500, body="Internal Server Error"))
    try:
        toast = _fail_test_connection_in_add_modal(page, mock_sonarr_url)
        _assert_toast_uncovered(toast)
        # Measure after the 10px slide-in animation settles.
        gap = toast.evaluate(
            """el => Promise.all(el.getAnimations().map(a => a.finished))
                .then(() => window.innerHeight - el.getBoundingClientRect().bottom)"""
        )
        assert 20 <= gap <= 40, f"toast sits {gap}px above the viewport bottom, expected 28"

        _close_add_modal_and_expect_page_toast(page)
        _assert_toast_uncovered(_fail_test_connection_in_add_modal(page, mock_sonarr_url))
    finally:
        page.unroute(pattern)


@pytest.mark.integration
def test_error_toast_handed_over_after_history_restore(
    logged_in_page: Page, houndarr_url: str, mock_sonarr_url: str, console_guard
) -> None:
    """Back navigation restores the instance modal with its toast copy from htmx's snapshot;
    closing that modal must still hand a showing message to the page toast."""
    for p in _EXPECTED_500_CONSOLE_NOISE:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")
    pattern = re.compile(r"/settings/instances/test-connection$")
    page.route(pattern, lambda route: route.fulfill(status=500, body="Internal Server Error"))
    try:
        _fail_test_connection_in_add_modal(page, mock_sonarr_url)
        _close_add_modal_and_expect_page_toast(page)
        expect(page.locator("#toast")).to_be_hidden(timeout=5_000)

        page.get_by_role("link", name="Dashboard", exact=True).first.click()
        page.wait_for_url(re.compile(rf"^{re.escape(houndarr_url)}/?$"))
        page.go_back()
        page.wait_for_url(re.compile(r"/settings$"))
        expect(page.locator("#add-instance-modal > .toast")).to_have_count(1)

        _fail_test_connection_in_add_modal(page, mock_sonarr_url)
        _close_add_modal_and_expect_page_toast(page)
    finally:
        page.unroute(pattern)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("failure", "motion", "message"),
    [
        ("http-500", "no-preference", "Request failed (HTTP 500)"),
        ("network", "reduce", "Could not reach Houndarr"),
    ],
)
def test_changelog_dismiss_error_toast_outlives_the_modal(
    logged_in_page: Page,
    houndarr_url: str,
    console_guard,
    failure: str,
    motion: str,
    message: str,
) -> None:
    """The changelog modal closes even when dismiss fails; its error toast must stay on screen."""
    for p in [*_EXPECTED_500_CONSOLE_NOISE, r"net::ERR_FAILED"]:
        console_guard.allow(p)
    page = logged_in_page
    page.emulate_media(reduced_motion=motion)
    page.goto(f"{houndarr_url}/settings")
    _open_admin_dropdown(page)
    page.get_by_role("button", name=re.compile(r"what'?s\s*new", re.I)).click()
    expect(page.locator("dialog#changelog-modal[open]")).to_be_visible(timeout=4_000)

    pattern = re.compile(r"/settings/changelog/dismiss$")
    page.route(
        pattern,
        lambda route: (
            route.fulfill(status=500, body="") if failure == "http-500" else route.abort("failed")
        ),
    )
    try:
        page.locator('[data-changelog-dismiss="true"]').click()
        expect(page.locator("dialog#changelog-modal")).to_have_count(0, timeout=4_000)
        expect(page.locator("#toast")).to_be_visible()
        expect(page.locator("#toast")).to_contain_text(message)
    finally:
        page.unroute(pattern)


@pytest.mark.integration
def test_error_toast_skips_gets_and_reports_unreachable_server(
    logged_in_page: Page, houndarr_url: str, console_guard
) -> None:
    """A failed GET stays silent (the dashboard polls); a mutation that never lands toasts.

    A 429 without Houndarr's ``X-Houndarr-Swap-Error`` marker stands in for
    a reverse proxy's rate-limit page: it must never replace the target, on
    a GET it stays silent, and on a mutation it gets the toast.
    """
    noise = [*_EXPECTED_500_CONSOLE_NOISE, *_EXPECTED_429_CONSOLE_NOISE, r"net::ERR_FAILED"]
    for p in noise:
        console_guard.allow(p)
    page = logged_in_page
    page.goto(f"{houndarr_url}/settings")

    def respond(route: Route) -> None:
        outcome = route.request.url.rsplit("=", 1)[-1]
        if outcome == "abort":
            route.abort("failed")
        else:
            route.fulfill(status=int(outcome), body="proxy rate-limit page")

    probe = re.compile(r"/settings/toast-probe\?outcome=")
    page.route(probe, respond)
    request = """([verb, outcome]) => htmx
        .ajax(verb, '/settings/toast-probe?outcome=' + outcome,
              {target: '#admin-flash', swap: 'innerHTML'})
        .then(() => 'done', () => 'done')"""
    try:
        page.evaluate(request, ["GET", "500"])
        expect(page.locator("#toast")).to_be_hidden()
        page.evaluate(request, ["GET", "429"])
        expect(page.locator("#admin-flash")).not_to_contain_text("proxy rate-limit page")
        expect(page.locator("#toast")).to_be_hidden()
        page.evaluate(request, ["POST", "429"])
        expect(page.locator("#admin-flash")).not_to_contain_text("proxy rate-limit page")
        expect(page.locator("#toast")).to_contain_text("Request failed (HTTP 429)")
        page.evaluate(request, ["POST", "abort"])
        expect(page.locator("#toast")).to_contain_text("Could not reach Houndarr", timeout=4_000)
    finally:
        page.unroute(probe)

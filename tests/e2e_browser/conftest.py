"""Browser e2e fixtures.

The workflow boots Houndarr in Docker and runs the mock *arr services in
sibling containers on the same Docker network, then invokes pytest on
the host with the URLs passed through environment variables.  These
fixtures read those values, attach a uniform console listener, and log
the test user in.  Nothing here starts or stops the services; the
workflow owns orchestration.
"""

from __future__ import annotations

import os
import re
from collections.abc import Generator
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import ConsoleMessage, Page

HOUNDARR_URL = os.environ.get("HOUNDARR_URL", "http://localhost:8877")
MOCK_SONARR_URL = os.environ.get("MOCK_SONARR_URL", "http://mock-sonarr:8989")
MOCK_RADARR_URL = os.environ.get("MOCK_RADARR_URL", "http://mock-radarr:7878")
ADMIN_USER = os.environ.get("HOUNDARR_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("HOUNDARR_E2E_PASS", "CITestPass1!")

# Console noise unrelated to any behaviour the suite verifies.
_ALLOWED_ERROR_PATTERNS = [
    # Google Fonts fetches abort in headless mode across every engine.
    re.compile(r"downloadable font: download failed"),
    # Webkit logs the bare HTMX event names (htmx:afterRequest,
    # htmx:sendError, htmx:responseError, htmx:swapError) to
    # console.error when an in-flight HTMX request is aborted by
    # location.reload() (e.g. HX-Refresh after a password change) or
    # by webkit's own navigation handling.  Chromium and firefox
    # squelch the same abort path silently.  The pagehide guard in
    # static/js/app.js cancels future requests during unload, but the
    # response that triggered the unload itself can still surface
    # mid-flight; allow the bare event-name console errors so the
    # autouse console_guard does not flag the teardown on webkit.
    re.compile(r"^htmx:[A-Za-z]+(?:Error|Request|Swap|Send|Response)?$"),
    # Sibling of the bare-event-name pattern above, but at the
    # ``pageerror`` (window-level) layer instead of console.error.
    # Webkit reports any in-flight fetch canceled by a pending
    # navigation as ``<URL> due to access control checks.`` even on
    # same-origin requests.  The recurring trigger for this suite is
    # the changelog popup's ``hx-trigger="load"`` GET still being in
    # flight when ``HX-Refresh`` after a password change calls
    # ``location.reload()``.  No client-side mitigation exists (the
    # browser fires the error on its own abort path), and chromium /
    # firefox swallow the same abort silently.  Documented upstream
    # in TanStack/router#719 and supabase/supabase#20982.  Scope the
    # match to the changelog popup path so a real auth failure on
    # any other endpoint still fails the teardown.
    re.compile(r"^pageerror:\s.*/settings/changelog/popup\b[^\n]*access control checks\.?$"),
]


@pytest.fixture(scope="session")
def houndarr_url() -> str:
    return HOUNDARR_URL


@pytest.fixture(scope="session")
def mock_sonarr_url() -> str:
    return MOCK_SONARR_URL


@pytest.fixture(scope="session")
def mock_radarr_url() -> str:
    return MOCK_RADARR_URL


@dataclass
class _ConsoleGuard:
    """Per-test control over the console-error allow list.

    Tests that intentionally trigger 4xx/5xx responses (HTMX logs the
    status code to ``console.error`` when ``error: true`` in the config)
    call ``allow(pattern)`` to whitelist the expected noise.  Everything
    else still fails the teardown assertion.
    """

    extra_patterns: list[re.Pattern[str]] = field(default_factory=list)

    def allow(self, pattern: str) -> None:
        self.extra_patterns.append(re.compile(pattern))


@pytest.fixture(autouse=True)
def console_guard(page: Page) -> Generator[_ConsoleGuard, None, None]:
    """Every browser test fails on console errors or uncaught JS exceptions.

    Applied via ``autouse`` so individual tests do not need to wire up the
    listener manually.  Filtered against ``_ALLOWED_ERROR_PATTERNS`` so
    pre-existing third-party noise does not flake the suite.  Tests that
    deliberately provoke error responses can request this fixture by
    name and call ``console_guard.allow(<pattern>)`` to whitelist them.
    """
    collected: list[str] = []
    guard = _ConsoleGuard()

    def on_console(msg: ConsoleMessage) -> None:
        if msg.type == "error":
            collected.append(msg.text)

    page.on("console", on_console)
    page.on("pageerror", lambda err: collected.append(f"pageerror: {err.message}"))

    yield guard

    allowed = _ALLOWED_ERROR_PATTERNS + guard.extra_patterns
    leftover = [e for e in collected if not any(p.search(e) for p in allowed)]
    assert not leftover, f"Unexpected console / page errors: {leftover}"


@pytest.fixture()
def logged_in_page(page: Page) -> Page:
    """A page with an authenticated session cookie."""
    page.goto(f"{HOUNDARR_URL}/login")
    page.get_by_role("textbox", name="Username").fill(ADMIN_USER)
    page.get_by_role("textbox", name="Password").fill(ADMIN_PASS)
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_url(re.compile(rf"^{re.escape(HOUNDARR_URL)}/?$"))
    return page

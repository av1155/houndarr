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

import pytest
from playwright.sync_api import ConsoleMessage, Page

HOUNDARR_URL = os.environ.get("HOUNDARR_URL", "http://localhost:8877")
MOCK_SONARR_URL = os.environ.get("MOCK_SONARR_URL", "http://mock-sonarr:8989")
MOCK_RADARR_URL = os.environ.get("MOCK_RADARR_URL", "http://mock-radarr:7878")
ADMIN_USER = os.environ.get("HOUNDARR_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("HOUNDARR_E2E_PASS", "CITestPass1!")

# Console noise that predates this workflow and is unrelated to any
# behaviour the suite verifies.  Every evergreen browser rejects the
# login template's HTML5 ``pattern`` attribute for using a character
# class that only validates under the ``v`` flag.  Chromium reports
# ``Pattern attribute value ... is not a valid regular expression``;
# Firefox reports ``Unable to check <input pattern=...> ... is not a
# valid regexp``; WebKit wraps the same message differently.  The
# shared substring is ``[-A-Za-z0-9_.]+`` with ``not a valid reg``.
# Google Fonts fetches also abort in headless mode.
_ALLOWED_ERROR_PATTERNS = [
    re.compile(r"\[-A-Za-z0-9_\.\]\+.*not a valid reg"),
    re.compile(r"downloadable font: download failed"),
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


@pytest.fixture(autouse=True)
def _fail_on_console_errors(page: Page) -> Generator[None, None, None]:
    """Every browser test fails on console errors or uncaught JS exceptions.

    Applied via ``autouse`` so individual tests do not need to wire up the
    listener manually.  Filtered against ``_ALLOWED_ERROR_PATTERNS`` so
    pre-existing third-party noise does not flake the suite.
    """
    collected: list[str] = []

    def on_console(msg: ConsoleMessage) -> None:
        if msg.type == "error":
            collected.append(msg.text)

    page.on("console", on_console)
    page.on("pageerror", lambda err: collected.append(f"pageerror: {err.message}"))

    yield

    leftover = [e for e in collected if not any(p.search(e) for p in _ALLOWED_ERROR_PATTERNS)]
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

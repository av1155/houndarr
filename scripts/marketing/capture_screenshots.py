"""Capture the marketing screenshot set from a running demo server.

Drives Playwright through a sequence of views and writes the PNG/JPEG
pair each docs page references. Assumes ``serve_demo.py`` is running on
``--base-url`` against a DB seeded by ``seed_demo_data.py``.

Views are split across two seed modes: the ``populated`` mode covers
everything except ``dashboard-empty``, which requires the ``empty`` seed.
The script accepts a ``--views`` filter so you can capture the subset
matching the DB you have booted.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page, async_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PNG_DIR = REPO_ROOT / "website" / "static" / "img" / "screenshots"
DEFAULT_JPEG_DIR = REPO_ROOT / "docs" / "images"

DEFAULT_SEED_MODE = "populated"


@dataclass(frozen=True, slots=True)
class View:
    """One captured view and its output filenames.

    ``png_name`` or ``jpeg_name`` may be ``None`` when only one format is
    needed. ``full_page=False`` caps the capture at the current viewport,
    which is right for hero shots that need to stay landscape.
    """

    name: str
    path: str
    wait_selector: str
    png_name: str | None
    jpeg_name: str | None
    viewport_width: int = 1440
    viewport_height: int = 900
    mode: str = "populated"
    settle_ms: int = 800
    full_page: bool = True


VIEWS: list[View] = [
    View(
        name="dashboard",
        path="/",
        wait_selector=".dash-card",
        png_name="houndarr-dashboard.png",
        jpeg_name="Dashboard_Houndarr.jpeg",
        settle_ms=1200,
    ),
    View(
        name="dashboard-empty",
        path="/",
        wait_selector=".dash-empty-state",
        png_name="houndarr-dashboard-empty.png",
        jpeg_name="Dashboard_Empty_Houndarr.jpeg",
        viewport_height=966,
        mode="empty",
    ),
    View(
        name="logs",
        path="/logs",
        wait_selector="#log-tbody",
        png_name="houndarr-logs.png",
        jpeg_name="Logs_Houndarr.jpeg",
    ),
    View(
        name="logs-mobile",
        path="/logs",
        wait_selector="#log-tbody",
        png_name="houndarr-logs-mobile.png",
        jpeg_name="Logs_Houndarr_Smartphone.jpeg",
        viewport_width=390,
        viewport_height=844,
    ),
    View(
        name="settings-instances",
        path="/settings",
        wait_selector="#instance-tbody",
        png_name="houndarr-settings-instances.png",
        jpeg_name="Settings_Houndarr.jpeg",
    ),
    View(
        name="settings-account",
        path="/settings",
        wait_selector="#account-section",
        png_name="houndarr-settings-account.png",
        jpeg_name="Settings_Account_Houndarr.jpeg",
    ),
    View(
        name="settings-help",
        path="/settings/help",
        wait_selector="main, #app-content",
        filename="houndarr-settings-help.png",
        full_page=False,
    ),
    View(
        name="add-instance",
        path="/settings",
        wait_selector="#instance-tbody",
        png_name="houndarr-add-instance-form.png",
        jpeg_name="Settings_Houndarr_Add_Instance_Settings.jpeg",
    ),
]


async def _login(page: Page, base_url: str, password: str) -> None:
    """Sign the admin user in once per browser session."""
    await page.goto(f"{base_url}/login")
    await page.fill('input[name="username"]', "admin")
    await page.fill('input[name="password"]', password)
    async with page.expect_navigation():
        await page.click('button[type="submit"]')
    await page.wait_for_selector("#app-content", timeout=10_000)


async def _prepare_view(page: Page, view: View) -> None:
    """Run any per-view interaction needed before screenshotting."""
    if view.name == "settings-account":
        # The password form lives in a collapsed <details>; expand it via
        # the .open property so we don't depend on summary text.
        await page.evaluate(
            "() => { const el = document.querySelector('#account-settings');"
            " if (el) el.open = true; }"
        )
        await page.wait_for_selector('form[hx-post="/settings/account/password"]', timeout=5_000)
    elif view.name == "add-instance":
        await page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
        await page.wait_for_selector('form[data-form-mode="add"]', state="visible", timeout=5_000)


async def _capture_view(
    page: Page, view: View, base_url: str, png_dir: Path, jpeg_dir: Path
) -> None:
    """Navigate to ``view``, wait for content, write the PNG to both dirs.

    Parks the mouse off-screen and blurs any focused element before the
    capture so hover highlights from prior interactions (e.g. the click
    in the ``add-instance`` view) and ``:focus-visible`` rings don't
    pollute the shot.
    """
    await page.set_viewport_size({"width": view.viewport_width, "height": view.viewport_height})
    await page.goto(f"{base_url}{view.path}")
    await page.wait_for_selector(view.wait_selector, timeout=10_000)
    await _prepare_view(page, view)
    await page.wait_for_timeout(view.settle_ms)

    # Park the mouse at the far bottom-right corner of the viewport and
    # clear any lingering focus so screenshots render a pristine state.
    await page.mouse.move(view.viewport_width - 1, view.viewport_height - 1)
    await page.evaluate(
        "() => { if (document.activeElement instanceof HTMLElement)"
        " document.activeElement.blur(); }"
    )
    # Give the browser one frame to repaint without :hover / :focus styles.
    await page.wait_for_timeout(80)

    wrote: list[str] = []
    if view.png_name is not None:
        png_dir.mkdir(parents=True, exist_ok=True)
        png_path = png_dir / view.png_name
        await page.screenshot(path=str(png_path), full_page=view.full_page, type="png")
        wrote.append(png_path.name)
    if view.jpeg_name is not None:
        jpeg_dir.mkdir(parents=True, exist_ok=True)
        jpeg_path = jpeg_dir / view.jpeg_name
        await page.screenshot(
            path=str(jpeg_path), full_page=view.full_page, type="jpeg", quality=92
        )
        wrote.append(jpeg_path.name)
    print(f"[capture] {view.name:20s} -> {' + '.join(wrote)}")


async def _run(
    base_url: str,
    views: list[View],
    password: str,
    png_dir: Path,
    jpeg_dir: Path,
) -> None:
    """Launch a single Chromium session and capture every requested view."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1.67,
            color_scheme="dark",
        )
        page = await ctx.new_page()
        await _login(page, base_url, password)
        for view in views:
            await _capture_view(page, view, base_url, png_dir, jpeg_dir)
        await browser.close()


def _select_views(names: list[str] | None, seed_mode: str) -> list[View]:
    """Pick which views to capture based on CLI flags + the booted seed mode."""
    by_name = {v.name: v for v in VIEWS}
    if names:
        selected = []
        for n in names:
            if n not in by_name:
                raise SystemExit(f"unknown view {n!r}. Choices: {sorted(by_name)}")
            selected.append(by_name[n])
    else:
        selected = list(VIEWS)
    # Drop views whose mode doesn't match the booted server.
    return [v for v in selected if v.mode == seed_mode]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8902",
        help="Running demo server URL (serve_demo.py default).",
    )
    parser.add_argument(
        "--password",
        default="E2EShot1!",
        help="Admin password set by seed_demo_data.py.",
    )
    parser.add_argument(
        "--seed-mode",
        choices=["populated", "empty"],
        default=DEFAULT_SEED_MODE,
        help="Matches the seed_demo_data.py --mode used for the booted DB.",
    )
    parser.add_argument(
        "--views",
        nargs="*",
        default=None,
        help="Subset of view names. Defaults to every view matching the seed mode.",
    )
    parser.add_argument("--png-dir", type=Path, default=DEFAULT_PNG_DIR)
    parser.add_argument("--jpeg-dir", type=Path, default=DEFAULT_JPEG_DIR)
    args = parser.parse_args()

    views = _select_views(args.views, args.seed_mode)
    if not views:
        raise SystemExit(f"no views left after filtering for seed mode {args.seed_mode!r}")
    asyncio.run(
        _run(
            args.base_url.rstrip("/"),
            views,
            args.password,
            args.png_dir.resolve(),
            args.jpeg_dir.resolve(),
        )
    )


if __name__ == "__main__":
    main()

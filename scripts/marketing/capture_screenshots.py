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
    hero: bool = False


VIEWS: list[View] = [
    View(
        name="dashboard",
        path="/",
        wait_selector=".dash-card",
        filename="houndarr-dashboard.png",
        settle_ms=1500,
        hero=True,
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
        wait_selector="#log-tbody tr",
        filename="houndarr-logs.png",
    ),
    View(
        name="logs-mobile",
        path="/logs",
        wait_selector="#log-tbody tr",
        filename="houndarr-logs-mobile.png",
        viewport_width=390,
        viewport_height=844,
        full_page=False,
    ),
    View(
        name="settings-instances",
        path="/settings",
        wait_selector="#instance-tbody tr",
        filename="houndarr-settings-instances.png",
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
    ),
    View(
        name="add-instance",
        path="/settings",
        wait_selector="#instance-tbody tr",
        filename="houndarr-add-instance-form.png",
        full_page=False,
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


async def _wait_for_htmx_idle(page: Page) -> None:
    """Poll until every HTMX-lifecycle class is gone from the DOM.

    HTMX marks in-flight XHRs with ``.htmx-request`` and transient
    swap/settle phases with ``.htmx-swapping`` / ``.htmx-settling`` /
    ``.htmx-added``. If we screenshot during any of those, we catch a
    half-rendered page (e.g. the dashboard grid missing its cards mid
    swap). Mirrors the pattern used by ``tests/e2e_browser/test_flows.py``
    so the capture script and the browser e2e suite agree on what
    ``idle`` means.
    """
    await page.wait_for_function(
        "document.querySelectorAll("
        "'.htmx-request, .htmx-settling, .htmx-swapping, .htmx-added'"
        ").length === 0",
        timeout=10_000,
    )


async def _prepare_view(page: Page, view: View) -> None:
    """Run any per-view interaction needed before screenshotting."""
    if view.name == "dashboard":
        # The dashboard renders each instance as a .dash-card inside
        # #instance-grid via a single HTMX innerHTML swap. Waiting for
        # one card is not enough: the grid briefly shows the pre-swap
        # empty state + one card can appear before htmx finishes
        # processing every child. Wait for the seeded population (six
        # enabled + one disabled) so we always capture the full grid.
        await page.wait_for_function(
            "document.querySelectorAll('#instance-grid .dash-card').length >= 7",
            timeout=10_000,
        )
        # Wait for the top subheader (polled side effect) to be populated
        # too so the "All N hounds on patrol." sentence is in the shot.
        await page.wait_for_selector("#dash-top .dash-sub__sentence", timeout=10_000)
    elif view.name == "settings-account":
        # The password form lives in a collapsed <details>. Clicking the
        # summary triggers the JS animation in settings_content.html which
        # flips the .account-details-content height/opacity; setting
        # `el.open = true` alone leaves the content at height:0 because
        # the page's own toggleAccountDetails handler never fires.
        await page.click("#account-settings > summary")
        await page.wait_for_selector(
            'form[hx-post="/settings/account/password"]',
            state="visible",
            timeout=5_000,
        )
        # Wait past the 180ms expand animation + a paint frame so the
        # form is fully laid out before we wrap the body for the shot.
        await page.wait_for_timeout(300)
        # Scroll the expanded form into view so the password fields sit
        # inside the full-page screenshot window.
        await page.evaluate(
            "() => { const el = document.querySelector('#account-settings');"
            " if (el) el.scrollIntoView({block:'end'}); }"
        )
        await page.wait_for_timeout(200)
    elif view.name == "add-instance":
        await page.get_by_role("button", name=re.compile(r"add\s*instance", re.I)).first.click()
        # showModal() opens the dialog in the top layer. Wait for both
        # the native [open] attribute and the HTMX-injected form so the
        # modal is fully hydrated before we shoot.
        await page.wait_for_selector("#add-instance-modal[open]", timeout=5_000)
        await page.wait_for_selector('form[data-form-mode="add"]', state="visible", timeout=5_000)
        await _wait_for_htmx_idle(page)
        # The modal animates in (180ms). Wait past that + a paint frame.
        await page.wait_for_timeout(300)


_WRAP_JS = """
(opts) => {
    const BASE = '#07080f';
    const SURFACE = '#0e1117';
    const isHero = opts.hero;
    const clipHeight = opts.clipHeight;  // null = full content, number = clip to viewport
    // Elements to leave at body level so their native positioning
    // (e.g. <dialog> top-layer for the add-instance modal) keeps
    // working. Moving them into the wrapper collapses the top-layer
    // entry and the modal would render at body-flow position.
    const preserveSelectors = opts.preserve || [];
    const body = document.body;
    const innerStyles = [
        'border-radius: 10px',
        'overflow: hidden',
        'box-shadow:',
        '  inset 0 0 0 1px rgba(34, 211, 238, ' + (isHero ? 0.08 : 0.06) + '),',
        '  0 0 0 1px rgba(30, 38, 56, 0.9),',
        '  0 8px 24px rgba(0, 0, 0, 0.55),',
        '  0 2px 4px rgba(0, 0, 0, 0.35),',
        '  0 0 ' + (isHero ? 60 : 28) + 'px rgba(34, 211, 238, ' + (isHero ? 0.15 : 0.08) + ')',
    ];
    if (clipHeight !== null) {
        innerStyles.push('max-height: ' + clipHeight + 'px');
    }
    const preserved = preserveSelectors
        .map(sel => document.querySelector(sel))
        .filter(el => el !== null);
    const content = document.createElement('div');
    content.id = '__shot-inner';
    content.style.cssText = innerStyles.join(';');
    const kids = Array.from(body.children);
    for (const child of kids) {
        if (preserved.includes(child)) continue;
        content.appendChild(child);
    }
    const outer = document.createElement('div');
    outer.id = '__shot-outer';
    const bg = isHero
        ? 'radial-gradient(ellipse at 50% 0%, ' + SURFACE + ' 0%, ' + BASE + ' 70%)'
        : BASE;
    outer.style.cssText = [
        'padding: ' + (isHero ? '56px' : '36px'),
        'background: ' + bg,
        'display: inline-block',
    ].join(';');
    outer.appendChild(content);
    body.insertBefore(outer, body.firstChild);
    body.style.margin = '0';
    body.style.background = BASE;
}
"""


async def _capture_view(
    page: Page, view: View, base_url: str, png_dir: Path, jpeg_dir: Path
) -> None:
    """Navigate to ``view``, wait for content, write the PNG to both dirs.

    Parks the mouse off-screen and blurs any focused element before the
    capture so hover highlights from prior interactions (e.g. the click
    in the ``add-instance`` view) and ``:focus-visible`` rings don't
    pollute the shot. Then wraps the page body in a Station-branded
    decorative frame (radial gradient + cyan inset highlight + ambient
    glow) and screenshots that wrapper so the README shots feel like
    Houndarr rather than a generic product.
    """
    await page.set_viewport_size({"width": view.viewport_width, "height": view.viewport_height})
    await page.goto(f"{base_url}{view.path}", wait_until="domcontentloaded")
    await page.wait_for_selector(view.wait_selector, timeout=10_000)
    # The dashboard's instance grid is rendered by an HTMX swap that
    # fires on load. wait_for_selector returns as soon as ONE match
    # appears, so without an HTMX-idle check we risk capturing the
    # page mid swap. Wait for the XHR to settle before preparing the
    # view-specific DOM tweaks.
    await _wait_for_htmx_idle(page)
    await page.wait_for_load_state("networkidle")
    # For the add-instance view, the wrap MUST happen before showModal()
    # fires. Reparenting a dialog's ancestor after the dialog has been
    # promoted to the top layer drops it back to flow position (verified
    # in Chromium 2026-04). Wrapping first means the dialog enters the
    # top layer inside the already-wrapped tree and renders centered on
    # the viewport as intended.
    if view.name != "add-instance":
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

    # Wrap body -> #__shot-outer > #__shot-inner > (original content). Token
    # values mirror the ones in src/houndarr/static/css/tokens.css so the
    # frame stays in step with the Station palette. For viewport-only
    # views (``full_page=False``) we clip the inner height so the wrapper
    # doesn't expand to include the full scrollable content.
    clip_height = None if view.full_page else view.viewport_height
    await page.evaluate(
        _WRAP_JS,
        {"hero": view.hero, "clipHeight": clip_height, "preserve": []},
    )

    # For the add-instance view, open the modal AFTER wrapping so the
    # dialog enters the top layer within the already-wrapped tree. The
    # resulting screenshot needs page.screenshot(clip=...) because the
    # top-layer dialog is painted over the wrapper but lives outside
    # the wrapper's DOM subtree.
    used_page_clip = False
    if view.name == "add-instance":
        await _prepare_view(page, view)
        await page.wait_for_timeout(view.settle_ms)
        # Re-park the mouse + blur so the "+ Add Instance" click doesn't
        # leave a hover or focus ring on the button in the final shot.
        await page.mouse.move(view.viewport_width - 1, view.viewport_height - 1)
        await page.evaluate(
            "() => { if (document.activeElement instanceof HTMLElement)"
            " document.activeElement.blur(); }"
        )
        await page.wait_for_timeout(80)
        used_page_clip = True

    wrote: list[str] = []
    wrapper = page.locator("#__shot-outer")
    if used_page_clip:
        box = await wrapper.bounding_box()
        if box is None:
            raise RuntimeError("wrapper lost its bounding box before capture")
        clip = {
            "x": box["x"],
            "y": box["y"],
            "width": box["width"],
            "height": box["height"],
        }
        for out_dir in (website_dir, readme_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / view.filename
            await page.screenshot(path=str(out_path), type="png", clip=clip)
            wrote.append(str(out_path.relative_to(REPO_ROOT)))
    else:
        for out_dir in (website_dir, readme_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / view.filename
            await wrapper.screenshot(path=str(out_path), type="png")
            wrote.append(str(out_path.relative_to(REPO_ROOT)))
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

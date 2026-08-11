"""Render the repository's star history as self-contained light and dark SVGs.

Reads ISO-8601 ``starred_at`` timestamps on stdin, one per line, and writes a
cumulative star-count chart in both themes. The README embeds the pair through
a ``<picture>`` block so each colour scheme gets readable contrast.

Rendering locally rather than embedding a hosted chart service is a security
decision, not a stylistic one. GitHub restricted the stargazers endpoint to a
repository's admins and collaborators in June 2026, and the permission that
satisfies that check is ``Contents: Read and write``. Any third-party embed
that reads this data on a visitor's behalf therefore needs a credential that
can also push to this repo, which is not something to park in a public README.
The scheduled workflow feeding this script uses the per-run Actions token
instead, which expires with the job.

Deliberately dependency-free so the workflow runs on the runner's stock Python
without provisioning the project virtualenv for a docs-only job.
"""

from __future__ import annotations

import argparse
import html
import math
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Geometry in user units. The SVG scales to its container, so only the
# relative proportions of these numbers matter.
WIDTH = 800
HEIGHT = 400
PAD_LEFT = 64
PAD_RIGHT = 28
PAD_TOP = 52
PAD_BOTTOM = 44

Y_TICKS = 4
X_TICKS = 5

# Past this many points the polyline gains no visible detail and only costs
# bytes, which matters because GitHub proxies README images through camo.
MAX_POINTS = 320

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


@dataclass(frozen=True, slots=True)
class Theme:
    """Colour set for one rendered variant."""

    name: str
    accent: str
    grid: str
    text: str
    title: str


# Cyan ramp taken from website/src/css/tokens.css (brand-700 light, brand-400
# dark) so the chart reads as part of the same product as the docs site.
THEMES = (
    Theme(name="light", accent="#0e7490", grid="#d8dee4", text="#57606a", title="#1f2328"),
    Theme(name="dark", accent="#22d3ee", grid="#30363d", text="#8b949e", title="#e6edf3"),
)

Point = tuple[datetime, int]


def parse_timestamps(lines: Iterable[str]) -> list[datetime]:
    """Parse one ISO-8601 timestamp per line, ignoring blanks, sorted ascending."""
    stamps = [datetime.fromisoformat(text) for line in lines if (text := line.strip())]
    stamps.sort()
    return stamps


def build_series(stamps: Sequence[datetime]) -> list[Point]:
    """Pair each star with the running total at the moment it was given."""
    return [(stamp, index) for index, stamp in enumerate(stamps, start=1)]


def downsample(series: Sequence[Point], limit: int = MAX_POINTS) -> list[Point]:
    """Thin the series to at most ``limit`` evenly spaced points."""
    # Two points are the minimum a line needs, and the interpolation below
    # divides by limit - 1.
    if limit < 2:
        return [series[-1]] if series else []
    if len(series) <= limit:
        return list(series)

    step = (len(series) - 1) / (limit - 1)
    picked = [series[round(index * step)] for index in range(limit)]
    # The final point carries the headline count, so rounding must never drop it.
    picked[-1] = series[-1]
    return picked


def nice_step(span: float, ticks: int) -> float:
    """Round a raw axis interval up to a human-readable 1/2/5 x 10^n step.

    Floored at 1 and restricted to integer factors because this axis counts
    stars: a gridline at 2.5 stars would be meaningless.
    """
    if span <= 0:
        return 1.0

    raw = span / ticks
    magnitude: float = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5):
        if raw <= factor * magnitude:
            return max(1.0, factor * magnitude)
    return max(1.0, 10 * magnitude)


def _x_tick_format(span: timedelta) -> str:
    """Pick the coarsest date format that still tells adjacent x ticks apart.

    Ticks are spread evenly across the window, so what governs collisions is
    the gap between them rather than the window itself. The one exception is
    the year: a day-and-month label repeats once the window reaches a year,
    however wide the gaps are.
    """
    gap = span / (X_TICKS - 1)
    if span >= timedelta(days=365):
        return "%b %Y"
    if gap >= timedelta(days=1):
        return "%b %d"
    if gap >= timedelta(minutes=1):
        return "%b %d %H:%M"
    return "%b %d %H:%M:%S"


def _scale_x(stamp: datetime, first: datetime, last: datetime) -> float:
    span = (last - first).total_seconds()
    usable = WIDTH - PAD_LEFT - PAD_RIGHT
    if span <= 0:
        return PAD_LEFT + usable
    return PAD_LEFT + (stamp - first).total_seconds() / span * usable


def _scale_y(count: float, top: float) -> float:
    usable = HEIGHT - PAD_TOP - PAD_BOTTOM
    if top <= 0:
        return HEIGHT - PAD_BOTTOM
    return HEIGHT - PAD_BOTTOM - count / top * usable


def _empty_svg(repo: str, theme: Theme) -> str:
    """Placeholder so a starless repo still yields a valid, embeddable file."""
    label = html.escape(repo)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="No stars yet for {label}">'
        f'<text x="{WIDTH / 2}" y="{HEIGHT / 2}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="16" fill="{theme.text}">No stars yet</text>'
        "</svg>"
    )


def render(series: Sequence[Point], repo: str, theme: Theme) -> str:
    """Build one complete SVG document for the given series and theme."""
    if not series:
        return _empty_svg(repo, theme)

    points = downsample(series)
    first, last = points[0][0], points[-1][0]
    total = series[-1][1]

    step = nice_step(total, Y_TICKS)
    top = math.ceil(total / step) * step

    parts: list[str] = []
    label = html.escape(repo)
    tick_format = _x_tick_format(last - first)
    # Below a second apart no format separates the ticks, and a window of zero
    # stacks them all on one pixel anyway, so draw the single moment instead.
    x_ticks = X_TICKS if (last - first) / (X_TICKS - 1) >= timedelta(seconds=1) else 1
    window = f"{first.strftime('%b %Y')} to {last.strftime('%b %Y')}"

    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" '
        f'aria-label="Star history for {label}: {total} stars, {window}">'
    )
    # Gradient ids are namespaced by theme so both files can coexist in one
    # document without the second one's fill resolving to the first's stop.
    parts.append(
        f'<defs><linearGradient id="area-{theme.name}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{theme.accent}" stop-opacity="0.22"/>'
        f'<stop offset="100%" stop-color="{theme.accent}" stop-opacity="0"/>'
        "</linearGradient></defs>"
    )

    parts.append(
        f'<text x="{PAD_LEFT}" y="28" font-family="{FONT}" font-size="15" '
        f'font-weight="600" fill="{theme.title}">{label}</text>'
    )
    parts.append(
        f'<text x="{WIDTH - PAD_RIGHT}" y="28" text-anchor="end" font-family="{FONT}" '
        f'font-size="13" fill="{theme.text}">{total} stars</text>'
    )

    tick = 0.0
    while tick <= top:
        y = _scale_y(tick, top)
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{y:.1f}" x2="{WIDTH - PAD_RIGHT}" y2="{y:.1f}" '
            f'stroke="{theme.grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 12}" y="{y + 4:.1f}" text-anchor="end" font-family="{FONT}" '
            f'font-size="12" fill="{theme.text}">{tick:,.0f}</text>'
        )
        tick += step

    for index in range(x_ticks):
        fraction = index / (x_ticks - 1) if x_ticks > 1 else 1.0
        stamp = first + (last - first) * fraction
        x = _scale_x(stamp, first, last)
        # Last-index first so the lone tick, which sits at the right edge,
        # anchors "end" rather than running off it.
        anchor = "end" if index == x_ticks - 1 else "start" if index == 0 else "middle"
        parts.append(
            f'<text x="{x:.1f}" y="{HEIGHT - PAD_BOTTOM + 22}" text-anchor="{anchor}" '
            f'font-family="{FONT}" font-size="12" fill="{theme.text}">'
            f"{stamp.strftime(tick_format)}</text>"
        )

    coords = [(_scale_x(stamp, first, last), _scale_y(count, top)) for stamp, count in points]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords))
    baseline = HEIGHT - PAD_BOTTOM

    parts.append(
        f'<path d="{line} L{coords[-1][0]:.1f} {baseline} L{coords[0][0]:.1f} {baseline} Z" '
        f'fill="url(#area-{theme.name})"/>'
    )
    parts.append(
        f'<path d="{line}" fill="none" stroke="{theme.accent}" stroke-width="2.5" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render star history SVGs from stdin timestamps.")
    parser.add_argument("--repo", required=True, help="owner/name, shown as the chart title")
    parser.add_argument(
        "--out-dir", type=Path, default=Path(), help="directory to write the SVGs into"
    )
    args = parser.parse_args()

    series = build_series(parse_timestamps(sys.stdin))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for theme in THEMES:
        target = args.out_dir / f"star-history-{theme.name}.svg"
        target.write_text(render(series, args.repo, theme), encoding="utf-8")
        print(f"wrote {target} ({len(series)} stars)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

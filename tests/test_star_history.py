"""Unit tests for the README star-history chart renderer.

The renderer runs unattended on a schedule and writes straight to a public
README, so the cheap invariants (integer star ticks, unique axis labels,
well-formed XML on degenerate input) are worth pinning here rather than
discovering them on the chart itself.

`scripts/` is outside the package, so the module is loaded by path using the
same repo-root discovery as `tests/test_gates/test_bootstrap_wiring_gate.py`.
"""

import importlib.util
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import houndarr

_REPO_ROOT = Path(houndarr.__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "star_history.py"

_spec = importlib.util.spec_from_file_location("star_history", _SCRIPT)
assert _spec is not None and _spec.loader is not None
star_history = importlib.util.module_from_spec(_spec)
# `@dataclass(slots=True)` resolves its own module from sys.modules while the
# class body executes, so registration has to precede exec_module.
sys.modules[_spec.name] = star_history
_spec.loader.exec_module(star_history)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _series(count, *, spacing_days=1, start=BASE):
    return star_history.build_series(
        [start + timedelta(days=i * spacing_days) for i in range(count)]
    )


def _series_spanning(days, *, count=200, start=BASE):
    """A series whose first and last points are exactly `days` apart."""
    step = timedelta(days=days) / (count - 1)
    return star_history.build_series([start + i * step for i in range(count)])


def _axis_labels(svg, axis):
    """Pull one axis's tick labels.

    Both axes render at the same font size, so they are told apart by the
    coordinate every tick on that axis shares: y-ticks sit at a fixed x, and
    x-ticks sit at a fixed y. These offsets mirror `render`.
    """
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    shared = {
        "y": ("x", str(star_history.PAD_LEFT - 12)),
        "x": ("y", str(star_history.HEIGHT - star_history.PAD_BOTTOM + 22)),
    }[axis]
    attr, value = shared
    return [el.text for el in root.iter(f"{ns}text") if el.get(attr) == value]


class TestNiceStep:
    """Star counts are integers, so the axis they sit on must be too."""

    def test_step_is_never_fractional(self):
        for total in range(1, 2000):
            step = star_history.nice_step(total, star_history.Y_TICKS)
            assert step >= 1.0, f"total={total} produced sub-unit step {step}"
            assert step == int(step), f"total={total} produced fractional step {step}"

    def test_degenerate_span_is_safe(self):
        assert star_history.nice_step(0, 4) == 1.0
        assert star_history.nice_step(-5, 4) == 1.0


class TestDownsample:
    def test_endpoints_survive(self):
        series = _series(5000)
        picked = star_history.downsample(series)
        assert picked[0] == series[0]
        assert picked[-1] == series[-1], "the final point carries the headline count"
        assert len(picked) <= star_history.MAX_POINTS

    def test_short_series_passes_through(self):
        series = _series(10)
        assert star_history.downsample(series) == series

    def test_counts_stay_monotonic(self):
        picked = star_history.downsample(_series(5000))
        counts = [count for _, count in picked]
        assert counts == sorted(counts)

    def test_limit_below_two_does_not_divide_by_zero(self):
        series = _series(10)
        assert star_history.downsample(series, 1) == [series[-1]]
        assert star_history.downsample([], 1) == []


class TestRender:
    """Every input must yield a parseable document; the workflow commits it blind."""

    def test_degenerate_inputs_stay_well_formed(self):
        theme = star_history.THEMES[0]
        cases = {
            "empty": [],
            "single": _series(1),
            "pair": _series(2),
            "same instant": star_history.build_series([BASE, BASE, BASE]),
            "large": _series(5000),
        }
        for name, series in cases.items():
            svg = star_history.render(series, "av1155/houndarr", theme)
            ET.fromstring(svg)  # raises on malformed XML
            assert svg.startswith("<svg"), name
            assert svg.endswith("</svg>"), name

    def test_repo_name_is_escaped(self):
        svg = star_history.render(_series(5), "owner/<script>&", star_history.THEMES[0])
        ET.fromstring(svg)
        assert "<script>" not in svg

    def test_star_ticks_are_whole_numbers(self):
        for total in (1, 2, 9, 10, 262, 1_000_000):
            svg = star_history.render(_series(total), "av1155/houndarr", star_history.THEMES[0])
            labels = _axis_labels(svg, "y")
            assert labels, f"total={total} rendered no star ticks"
            for label in labels:
                bare = label.replace(",", "")
                assert bare.isdigit(), f"total={total} produced non-integer tick {label!r}"

    def test_date_labels_are_unique_across_a_year_boundary(self):
        # A day-and-month tick repeats once the window spans more than a year,
        # which rendered the same date twice on one axis. Spans are sampled on
        # both sides of the boundary and inside the range the old
        # 730-day threshold left exposed.
        for span in (200, 366, 500, 729, 1100, 3000):
            svg = star_history.render(
                _series_spanning(span), "av1155/houndarr", star_history.THEMES[0]
            )
            labels = _axis_labels(svg, "x")
            assert len(labels) == star_history.X_TICKS
            assert len(labels) == len(set(labels)), f"span={span} duplicated labels: {labels}"

    def test_both_themes_namespace_their_gradient(self):
        series = _series(50)
        ids = {
            f'id="area-{theme.name}"'
            for theme in star_history.THEMES
            if f'id="area-{theme.name}"' in star_history.render(series, "av1155/houndarr", theme)
        }
        assert len(ids) == len(star_history.THEMES)

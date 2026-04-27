"""Track E gate: every Jinja macro file landed and the A.22 pinning suite is present.

The per-batch pinning tests cover each individual macro's
byte-equal contract.  This gate locks the Strangler-Fig
invariant that every E batch actually landed: each `_macros/*.html`
file exists with the right macros declared, and the Track A.22
render-pinning suite that pins consumer-level structural markers
is still in place.  A later track cannot silently delete a macro,
rename one, or drop the parity suite without this test failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import houndarr

pytestmark = pytest.mark.pinning


# REPO_ROOT / src / houndarr / __init__.py  ->  REPO_ROOT
_REPO_ROOT = Path(houndarr.__file__).resolve().parents[2]

_MACROS_DIR = _REPO_ROOT / "src" / "houndarr" / "templates" / "_macros"
_PINNING_DIR = _REPO_ROOT / "tests" / "test_templates"
_RENDER_HARNESS = _PINNING_DIR / "test_pinned_render.py"

# Track E delivers seven macro files.  The Track F batch landed an
# eighth (htmx.html), but it has its own gate; this one only enforces
# the Track E surface.
_E_MACROS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "badges.html",
        (
            "instance_type_badge",
            "log_action_badge",
            "log_kind_badge",
            "log_trigger_badge",
            "log_cycle_outcome_badge",
            "status_pill",
        ),
    ),
    (
        "forms.html",
        (
            "form_field",
            "password_input",
            "checkbox",
            "select_field",
        ),
    ),
    ("alerts.html", ("alert",)),
    ("buttons.html", ("btn",)),
    ("modals.html", ("dialog_shell", "confirm_dialog_shell")),
    ("instances.html", ("instance_row", "form_context")),
    ("layout.html", ("admin_section",)),
)

# Pinning files for each Track E batch.  test_pinned_render.py is the
# A.22 render harness; the rest are the byte-equal macro pinning
# suites added by E.1, E.6, E.13, E.14, E.15, E.16, E.17.
_E_PINNING_FILES: tuple[str, ...] = (
    "test_pinned_render.py",
    "test_macros_badges.py",
    "test_macros_forms.py",
    "test_macros_alerts.py",
    "test_macros_buttons.py",
    "test_macros_modals.py",
    "test_macros_instances.py",
    "test_macros_layout.py",
)


class TestMacroFilesPresent:
    """Every Track E macro file lives at the expected path."""

    @pytest.mark.parametrize(
        "filename",
        [name for name, _ in _E_MACROS],
    )
    def test_macro_file_exists(self, filename: str) -> None:
        path = _MACROS_DIR / filename
        assert path.is_file(), f"Track E macro file missing at {path.relative_to(_REPO_ROOT)}"


class TestMacroDefinitions:
    """Every macro the plan listed for the file is declared in the file."""

    @pytest.mark.parametrize(
        ("filename", "macro_name"),
        [(filename, macro_name) for filename, macros in _E_MACROS for macro_name in macros],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_macro_declared_in_file(self, filename: str, macro_name: str) -> None:
        path = _MACROS_DIR / filename
        source = path.read_text()
        # Jinja macro declarations look like `{%- macro NAME(...)` or
        # `{% macro NAME(...)`; pin the bare prefix so a future edit
        # cannot rename the macro without this assertion catching it.
        assert f"macro {macro_name}(" in source, (
            f"Macro `{macro_name}` not declared in {path.relative_to(_REPO_ROOT)}"
        )


class TestPinningSuitesPresent:
    """The A.22 render harness and every per-macro pinning suite is in place."""

    @pytest.mark.parametrize("filename", _E_PINNING_FILES)
    def test_pinning_file_exists(self, filename: str) -> None:
        path = _PINNING_DIR / filename
        assert path.is_file(), f"Track E pinning file missing at {path.relative_to(_REPO_ROOT)}"

    def test_render_harness_imports_jinja_environment(self) -> None:
        # The harness drives every consumer-level pinning test in
        # test_pinned_render.py; if the harness changes shape, the
        # pinning tests stop matching the post-migration HTML.
        source = (_PINNING_DIR / "conftest.py").read_text()
        assert "FileSystemLoader" in source
        assert 'env.filters["changelog_bullet"]' in source


class TestNoDecorativeBannerComments:
    """Track E macro files use informative comment blocks, not decorative banners.

    The commenting standard forbids ASCII section banners
    (`# ====` / `# ----`).  Each macro file ships a single
    `{#- ... -#}` doc block at the top followed by per-macro
    `{#- ... -#}` doc blocks.
    """

    @pytest.mark.parametrize(
        "filename",
        [name for name, _ in _E_MACROS],
    )
    def test_no_banner_dividers(self, filename: str) -> None:
        source = (_MACROS_DIR / filename).read_text()
        assert "===== " not in source, f"{filename} contains a `===== ...` banner divider"
        assert "----- " not in source, f"{filename} contains a `----- ...` banner divider"

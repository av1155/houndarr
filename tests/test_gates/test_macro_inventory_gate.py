"""Consolidated invariant: the Jinja macro inventory stays whole.

Each per-macro pinning test covers one macro's byte-equal render.
This gate locks the layer above them: each ``_macros/*.html`` file
exists, the expected macro names are declared inside, and the
render-pinning harness that pins consumer-level structural markers
is still in place.  A silent macro deletion, rename, or harness
removal fails here loudly.
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

# Seven macro files make up the shared template surface.  An
# eighth file (``htmx.html``) is covered by the HTMX contract gate,
# so this gate only enforces these seven.
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

# Pinning files for the macro surface.  ``test_pinned_render.py`` is
# the consumer-level render harness; the rest are per-macro
# byte-equal pinning suites.
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
    """Every declared macro file lives at the expected path."""

    @pytest.mark.parametrize(
        "filename",
        [name for name, _ in _E_MACROS],
    )
    def test_macro_file_exists(self, filename: str) -> None:
        path = _MACROS_DIR / filename
        assert path.is_file(), f"Macro file missing at {path.relative_to(_REPO_ROOT)}"


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
    """The render harness and every per-macro pinning suite is in place."""

    @pytest.mark.parametrize("filename", _E_PINNING_FILES)
    def test_pinning_file_exists(self, filename: str) -> None:
        path = _PINNING_DIR / filename
        assert path.is_file(), f"Pinning file missing at {path.relative_to(_REPO_ROOT)}"

    def test_render_harness_imports_jinja_environment(self) -> None:
        # The harness drives every consumer-level pinning test in
        # test_pinned_render.py; if the harness changes shape, the
        # pinning tests stop matching the rendered HTML.
        source = (_PINNING_DIR / "conftest.py").read_text()
        assert "FileSystemLoader" in source
        assert 'env.filters["changelog_bullet"]' in source


class TestNoDecorativeBannerComments:
    """Macro files use informative comment blocks, not decorative banners.

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

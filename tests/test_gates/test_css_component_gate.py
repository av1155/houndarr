"""Consolidated invariant: the CSS component and utility layer stays whole.

The per-macro pinning tests (test_macros_forms, test_macros_badges)
and the built-bundle hash (test_css_hash_pinning) cover the
consumer surface.  This gate locks the CSS structure above them:
the ``@layer components`` block declares the ``.field-label`` and
``.status-pill`` rules, the four ``@utility`` rules back the
inline-shadow and brand-rule macros, no ``duration-slow`` utility
leaks into the build, and the off-limits auth CSS sentinels are
in place.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import houndarr

pytestmark = pytest.mark.pinning


_REPO_ROOT = Path(houndarr.__file__).resolve().parents[2]

_INPUT_CSS = _REPO_ROOT / "src" / "houndarr" / "static" / "css" / "input.css"
_APP_BUILT_CSS = _REPO_ROOT / "src" / "houndarr" / "static" / "css" / "app.built.css"
_SHA256_PIN = _REPO_ROOT / "tests" / "_artifacts" / "app.built.css.sha256"
_ARTIFACTS_README = _REPO_ROOT / "tests" / "_artifacts" / "README.md"

_AUTH_CSS = _REPO_ROOT / "src" / "houndarr" / "static" / "css" / "auth.css"
_AUTH_FIELDS_CSS = _REPO_ROOT / "src" / "houndarr" / "static" / "css" / "auth-fields.css"

_FORMS_MACRO = _REPO_ROOT / "src" / "houndarr" / "templates" / "_macros" / "forms.html"
_BADGES_MACRO = _REPO_ROOT / "src" / "houndarr" / "templates" / "_macros" / "badges.html"
_TEMPLATES_DIR = _REPO_ROOT / "src" / "houndarr" / "templates"

_TRACK_G_NOTES = _REPO_ROOT / "docs" / "refactor" / "track-g-notes.md"


class TestLayerComponents:
    """input.css declares @layer components with `.field-label` + `.status-pill`."""

    def test_layer_components_block_present(self) -> None:
        source = _INPUT_CSS.read_text()
        assert "@layer components {" in source, "input.css missing @layer components block"

    def test_field_label_rule_present(self) -> None:
        source = _INPUT_CSS.read_text()
        assert ".field-label {" in source
        expected_apply = (
            "@apply block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wide"
        )
        assert expected_apply in source

    @pytest.mark.parametrize(
        ("selector", "applied"),
        [
            (
                ".status-pill {",
                "@apply inline-flex items-center justify-center gap-1 text-xs min-w-[4.5rem]",
            ),
            (".status-pill--active", "@apply text-success"),
            (".status-pill--error", "@apply text-danger"),
            (".status-pill--disabled", "@apply text-slate-500"),
        ],
    )
    def test_status_pill_rule_present(self, selector: str, applied: str) -> None:
        source = _INPUT_CSS.read_text()
        assert selector in source, f"input.css missing status-pill rule {selector!r}"
        assert applied in source, f"input.css missing @apply body for {selector!r}"


class TestUtilityRules:
    """Four @utility rules back the shadow and brand-rule surface."""

    @pytest.mark.parametrize(
        ("name", "declaration"),
        [
            ("logo-glow", "filter: drop-shadow(var(--glow-logo))"),
            ("shadow-4", "box-shadow: var(--shadow-4)"),
            (
                "shadow-modal",
                "box-shadow: 0 30px 80px rgba(0, 0, 0, 0.7), 0 0 0 1px rgba(30, 38, 56, 0.8)",
            ),
            ("border-l-brand-rule", "border-left: 3px solid var(--color-brand-500)"),
        ],
    )
    def test_utility_defined(self, name: str, declaration: str) -> None:
        source = _INPUT_CSS.read_text()
        assert f"@utility {name}" in source, f"input.css missing @utility {name}"
        assert declaration in source, (
            f"input.css @utility {name} body changed; expected {declaration!r}"
        )

    def test_duration_slow_utility_removed(self) -> None:
        # No template consumes ``duration-slow``; reintroducing it
        # should come with a real consumer, not as an orphan utility.
        source = _INPUT_CSS.read_text()
        assert "@utility duration-slow" not in source, (
            "duration-slow @utility came back; add a template consumer first or leave it out"
        )


class TestInlineStylesMigrated:
    """Templates ship no shadow / filter / border-left inline styles."""

    @pytest.mark.parametrize(
        "needle",
        ['style="box-shadow', 'style="filter:', 'style="border-left:'],
    )
    def test_no_inline_style(self, needle: str) -> None:
        hits: list[str] = []
        for path in _TEMPLATES_DIR.rglob("*.html"):
            text = path.read_text()
            if needle in text:
                hits.append(str(path.relative_to(_REPO_ROOT)))
        assert not hits, (
            f"inline {needle!r} returned in: {hits}; migrate it to the matching @utility"
        )


class TestMacroDefaults:
    """forms.html + badges.html macros emit the new component class names."""

    def test_form_field_default_label_class(self) -> None:
        source = _FORMS_MACRO.read_text()
        # Two macros share the default string: form_field + select_field.
        assert source.count('label_class="field-label"') == 2, (
            "form_field / select_field default label_class drifted away from 'field-label'"
        )

    def test_form_field_no_longer_inlines_label_bundle_in_defaults(self) -> None:
        # The bundle may still appear inside an explicit override (confirm-dialog
        # admin variant keeps `block text-xs font-medium text-slate-400 mb-1.5`
        # without uppercase/tracking-wide), so we assert only that the full
        # uppercase-tracking-wide bundle no longer lives on the default line.
        source = _FORMS_MACRO.read_text()
        default_bundle = '"block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wide"'
        assert default_bundle not in source, (
            "form_field / select_field default label_class should be 'field-label'; "
            "the inline utility bundle is back"
        )

    @pytest.mark.parametrize(
        "modifier",
        [
            "status-pill status-pill--active",
            "status-pill status-pill--error",
            "status-pill status-pill--disabled",
        ],
    )
    def test_status_pill_macro_emits_component_classes(self, modifier: str) -> None:
        source = _BADGES_MACRO.read_text()
        assert f'<span class="{modifier}">' in source, (
            f"status_pill macro stopped emitting `{modifier}`"
        )


class TestCssHashArtifact:
    """tests/_artifacts/app.built.css.sha256 is a committed reference."""

    def test_sha256_pin_exists(self) -> None:
        assert _SHA256_PIN.is_file()

    def test_sha256_pin_is_single_line_hash(self) -> None:
        raw = _SHA256_PIN.read_text(encoding="utf-8").strip()
        head = raw.split()[0]
        assert len(head) == 64
        int(head, 16)  # parse as hex

    def test_readme_documents_refresh_policy(self) -> None:
        assert _ARTIFACTS_README.is_file()
        body = _ARTIFACTS_README.read_text()
        assert "app.built.css.sha256" in body
        assert "pnpm run build-css" in body

    def test_bundle_matches_pin_when_bundle_present(self) -> None:
        if not _APP_BUILT_CSS.is_file():
            pytest.skip("app.built.css not present; run `pnpm run build-css`")
        actual = hashlib.sha256(_APP_BUILT_CSS.read_bytes()).hexdigest()
        expected = _SHA256_PIN.read_text(encoding="utf-8").strip().split()[0]
        assert actual == expected, "app.built.css hash drift without a matching sha256 pin update"


class TestDocsAndSentinels:
    """CSS docs and off-limits sentinels are in place."""

    def test_track_g_notes_exist(self) -> None:
        assert _TRACK_G_NOTES.is_file()
        body = _TRACK_G_NOTES.read_text()
        # The notes file documents why the log action-badge surface
        # stays out of the shared macro layer.
        assert "action-badge" in body

    def test_auth_css_has_off_limits_sentinel(self) -> None:
        body = _AUTH_CSS.read_text()
        assert "OFF-LIMITS" in body

    def test_auth_fields_css_has_off_limits_sentinel(self) -> None:
        body = _AUTH_FIELDS_CSS.read_text()
        assert "OFF-LIMITS" in body

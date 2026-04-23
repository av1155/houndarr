"""Track F gate: HTMX macros + helpers landed and HX parity suite is present.

The per-batch pinning tests cover each individual helper's and
macro's byte-equal contract.  This gate locks the Strangler-Fig
invariant that every F batch actually landed: the shared macros
file exists with both macros defined, routes/_htmx.py exports the
five response helpers (plus the pre-existing is_hx_request
request-side check), and the HX-header parity suite that pins
consumer routes is still in place.  A later track cannot silently
delete a helper, rename a macro, or drop the parity suite without
this test failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import houndarr
from houndarr.routes import _htmx as htmx_module

pytestmark = pytest.mark.pinning


# REPO_ROOT / src / houndarr / __init__.py  ->  REPO_ROOT
_REPO_ROOT = Path(houndarr.__file__).resolve().parents[2]

_MACROS_FILE = _REPO_ROOT / "src" / "houndarr" / "templates" / "_macros" / "htmx.html"
_HX_HEADERS_PIN = _REPO_ROOT / "tests" / "test_routes" / "test_hx_headers_pinning.py"
_HX_HELPERS_PIN = _REPO_ROOT / "tests" / "test_routes" / "test_hx_helpers_pinning.py"
_MACROS_PIN = _REPO_ROOT / "tests" / "test_templates" / "test_macros_htmx.py"

# Every F.3 consumer route has to pull the helper from the shared module,
# not reimplement it locally.  If one of these imports disappears the
# consumer has regressed to hand-rolling the header.
_F3_CONSUMERS: tuple[tuple[Path, str], ...] = (
    (
        _REPO_ROOT / "src" / "houndarr" / "routes" / "settings" / "_helpers.py",
        "from houndarr.routes._htmx import",
    ),
    (
        _REPO_ROOT / "src" / "houndarr" / "routes" / "admin.py",
        "from houndarr.routes._htmx import hx_redirect_response",
    ),
    (
        _REPO_ROOT / "src" / "houndarr" / "routes" / "changelog.py",
        "from houndarr.routes._htmx import hx_trigger_after_swap",
    ),
    (
        _REPO_ROOT / "src" / "houndarr" / "routes" / "settings" / "account.py",
        "from houndarr.routes._htmx import hx_refresh_response",
    ),
)


class TestMacrosFile:
    """_macros/htmx.html exists and declares the two macros F.2 migrated to."""

    def test_file_exists(self) -> None:
        assert _MACROS_FILE.is_file(), (
            f"Track F.1 macro file missing at {_MACROS_FILE.relative_to(_REPO_ROOT)}"
        )

    def test_shell_nav_link_defined(self) -> None:
        source = _MACROS_FILE.read_text()
        assert "macro shell_nav_link(" in source

    def test_hx_shell_fetch_defined(self) -> None:
        source = _MACROS_FILE.read_text()
        assert "macro hx_shell_fetch(" in source


class TestHtmxHelperExports:
    """routes/_htmx.py exposes the five response helpers + is_hx_request."""

    @pytest.mark.parametrize(
        "name",
        [
            "is_hx_request",
            "hx_refresh_response",
            "hx_redirect_response",
            "hx_trigger_response",
            "hx_trigger_after_swap",
            "hx_retarget_response",
        ],
    )
    def test_symbol_importable(self, name: str) -> None:
        assert hasattr(htmx_module, name), (
            f"routes/_htmx.py missing symbol {name!r}; Track F.3 did not land cleanly"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "hx_refresh_response",
            "hx_redirect_response",
            "hx_trigger_response",
            "hx_trigger_after_swap",
            "hx_retarget_response",
        ],
    )
    def test_symbol_callable(self, name: str) -> None:
        assert callable(getattr(htmx_module, name))


class TestHxParitySuite:
    """The HX-header parity test suite is still present, along with the
    helper-level pinning the plan asked for in F.3.
    """

    def test_hx_headers_pinning_file_exists(self) -> None:
        assert _HX_HEADERS_PIN.is_file(), (
            f"A.23 parity suite missing at {_HX_HEADERS_PIN.relative_to(_REPO_ROOT)}"
        )

    def test_hx_helpers_pinning_file_exists(self) -> None:
        assert _HX_HELPERS_PIN.is_file(), (
            f"F.3 helper pinning missing at {_HX_HELPERS_PIN.relative_to(_REPO_ROOT)}"
        )

    def test_macros_pinning_file_exists(self) -> None:
        assert _MACROS_PIN.is_file(), (
            f"F.1 macro pinning missing at {_MACROS_PIN.relative_to(_REPO_ROOT)}"
        )


class TestF3ConsumersImportHelpers:
    """Each F.3 consumer route imports from the shared helper module."""

    @pytest.mark.parametrize(
        ("consumer_path", "expected_import"),
        _F3_CONSUMERS,
        ids=lambda v: v.name if isinstance(v, Path) else v,
    )
    def test_consumer_imports_from_htmx(
        self,
        consumer_path: Path,
        expected_import: str,
    ) -> None:
        source = consumer_path.read_text()
        assert expected_import in source, (
            f"{consumer_path.relative_to(_REPO_ROOT)} must import from _htmx"
        )

    def test_helpers_consumer_imports_retarget_and_trigger(self) -> None:
        # settings/_helpers.py is the only consumer using two helpers;
        # the parametrised import check above only asserts the bare
        # prefix, so pin the full pair here to prevent a half-rollback.
        source = _F3_CONSUMERS[0][0].read_text()
        assert "hx_retarget_response" in source
        assert "hx_trigger_response" in source

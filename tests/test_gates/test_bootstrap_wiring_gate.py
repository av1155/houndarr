"""Consolidated invariant: bootstrap_non_web is importable and wired everywhere.

The pinning tests in ``tests/test_bootstrap/`` cover
:func:`bootstrap_non_web`'s own behavioural contract.  This gate
locks the layer above them: the three non-web entry points (the
``python -m houndarr`` CLI, ``scripts/marketing/seed_demo_data.py``,
``scripts/marketing/serve_demo.py``) all import the shared
primitive, and the primitive itself keeps its expected signature
shape.  A caller that drops back to an inline composition fails
here loudly.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import houndarr
from houndarr.bootstrap import AppSettingsOverrides, bootstrap_non_web

pytestmark = pytest.mark.pinning

# REPO_ROOT / src / houndarr / __init__.py  ->  REPO_ROOT
_REPO_ROOT = Path(houndarr.__file__).resolve().parents[2]

# Every non-web entry point must delegate to the shared bootstrap.
_CALL_SITES: tuple[Path, ...] = (
    _REPO_ROOT / "src" / "houndarr" / "__main__.py",
    _REPO_ROOT / "scripts" / "marketing" / "seed_demo_data.py",
    _REPO_ROOT / "scripts" / "marketing" / "serve_demo.py",
)

_IMPORT_LINE = "from houndarr.bootstrap import bootstrap_non_web"


class TestImportability:
    """The shared primitive is reachable from its documented home."""

    def test_callable(self) -> None:
        assert callable(bootstrap_non_web)

    def test_module_path(self) -> None:
        assert bootstrap_non_web.__module__ == "houndarr.bootstrap"

    def test_name(self) -> None:
        assert bootstrap_non_web.__name__ == "bootstrap_non_web"


class TestSignatureShape:
    """Pin the public signature of :func:`bootstrap_non_web`."""

    def test_data_dir_is_first_positional(self) -> None:
        sig = inspect.signature(bootstrap_non_web)
        params = list(sig.parameters.values())
        # data_dir must be the first parameter.
        assert params[0].name == "data_dir"
        assert params[0].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        )

    def test_data_dir_typed_as_str(self) -> None:
        sig = inspect.signature(bootstrap_non_web)
        # With ``from __future__ import annotations`` the annotation is a str.
        assert sig.parameters["data_dir"].annotation == "str"

    def test_has_var_keyword_overrides(self) -> None:
        sig = inspect.signature(bootstrap_non_web)
        var_keyword = [
            p for p in sig.parameters.values() if p.kind == inspect.Parameter.VAR_KEYWORD
        ]
        assert len(var_keyword) == 1
        assert var_keyword[0].name == "overrides"

    def test_overrides_typed_as_app_settings_overrides(self) -> None:
        sig = inspect.signature(bootstrap_non_web)
        annotation = str(sig.parameters["overrides"].annotation)
        # Unpack[AppSettingsOverrides] is how the TypedDict is spread.
        assert "AppSettingsOverrides" in annotation
        assert "Unpack" in annotation

    def test_return_annotation_is_tuple_shape(self) -> None:
        sig = inspect.signature(bootstrap_non_web)
        annotation = str(sig.return_annotation)
        assert "tuple" in annotation
        assert "AppSettings" in annotation
        assert "Path" in annotation
        assert "bytes" in annotation


class TestOverridesTypedDict:
    """Pin the :class:`AppSettingsOverrides` TypedDict surface."""

    def test_is_typed_dict_with_total_false(self) -> None:
        # __total__ is False because every key is optional.
        assert AppSettingsOverrides.__total__ is False

    def test_has_every_documented_override_key(self) -> None:
        keys = set(AppSettingsOverrides.__annotations__)
        # Every non-data_dir field on AppSettings must be an override key.
        assert keys == {
            "host",
            "port",
            "dev",
            "log_level",
            "secure_cookies",
            "cookie_samesite",
            "trusted_proxies",
            "auth_mode",
            "auth_proxy_header",
            "update_check_repo",
            "log_retention_days",
        }


class TestMigratedCallSites:
    """Every non-web entry point must import the shared primitive."""

    @pytest.mark.parametrize("call_site", _CALL_SITES, ids=lambda p: p.name)
    def test_imports_bootstrap_non_web(self, call_site: Path) -> None:
        source = call_site.read_text()
        assert _IMPORT_LINE in source, f"{call_site} must import bootstrap_non_web"

    @pytest.mark.parametrize("call_site", _CALL_SITES, ids=lambda p: p.name)
    def test_calls_bootstrap_non_web(self, call_site: Path) -> None:
        # Also confirm the imported symbol is invoked, not just imported,
        # so a later accidental dead-code import cannot pass this gate.
        source = call_site.read_text()
        assert "bootstrap_non_web(" in source, f"{call_site} must invoke bootstrap_non_web"

    def test_main_no_longer_hand_constructs_appsettings(self) -> None:
        # ``bootstrap_non_web`` owns ``AppSettings`` construction;
        # ``__main__.py`` delegates to it instead of instantiating
        # ``AppSettings`` directly.
        source = (_REPO_ROOT / "src" / "houndarr" / "__main__.py").read_text()
        assert "AppSettings(" not in source, (
            "__main__.py must delegate AppSettings construction to bootstrap_non_web"
        )

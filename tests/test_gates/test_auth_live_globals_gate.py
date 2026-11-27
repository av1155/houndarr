"""Tests never assign ``houndarr.auth``'s live globals on the package.

``houndarr.auth`` resolves ``_serializer``, ``_setup_complete`` and
``_USERNAME_PATTERN`` through a module ``__getattr__``.  Assigning one on
the package creates a real attribute that hides it, so every later test on
the same xdist worker reads the stale value.  Reset them through
``reset_auth_caches()`` or the owning submodule instead.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.pinning

_TESTS = Path(__file__).resolve().parents[1]
_REPO_ROOT = _TESTS.parent
_LIVE_NAMES = frozenset({"_serializer", "_setup_complete", "_USERNAME_PATTERN"})
_LIVE_PATHS = frozenset(f"houndarr.auth.{name}" for name in _LIVE_NAMES)

# xdist and pytest set these in the parent run; the child is a standalone run.
_ENV = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}


def _package_aliases(tree: ast.Module) -> set[str]:
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name == "houndarr.auth")
        elif isinstance(node, ast.ImportFrom) and node.module == "houndarr":
            aliases.update(a.asname or a.name for a in node.names if a.name == "auth")
    return aliases


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def shadowing_writes(source: str) -> list[str]:
    """Return statements that set a live global as a ``houndarr.auth`` attribute."""
    tree = ast.parse(source)
    packages = _package_aliases(tree) | {"houndarr.auth"}
    found = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr in _LIVE_NAMES
                and _dotted(target.value) in packages
            ):
                found.append(ast.unparse(node))
        if (
            isinstance(node, ast.Call)
            and _dotted(node.func).endswith("setattr")
            and len(node.args) >= 2
        ):
            owner, name = node.args[0], node.args[1]
            if (
                _dotted(owner) in packages
                and isinstance(name, ast.Constant)
                and name.value in _LIVE_NAMES
            ) or (isinstance(owner, ast.Constant) and owner.value in _LIVE_PATHS):
                found.append(ast.unparse(node))
    return found


def test_no_test_shadows_auth_live_globals() -> None:
    found = {
        str(p.relative_to(_REPO_ROOT)): writes
        for p in sorted(_TESTS.rglob("*.py"))
        if (writes := shadowing_writes(p.read_text(encoding="utf-8")))
    }
    assert found == {}


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("import houndarr.auth as _auth_mod\n_auth_mod._serializer = None", True),
        ("from houndarr import auth\nauth._setup_complete = True", True),
        ("import houndarr.auth\nhoundarr.auth._USERNAME_PATTERN = None", True),
        ("import houndarr.auth as a\nmonkeypatch.setattr(a, '_serializer', None)", True),
        ("monkeypatch.setattr('houndarr.auth._setup_complete', None)", True),
        ("import houndarr.auth.session as s\ns._serializer = None", False),
        ("import houndarr.auth as _auth_mod\n_auth_mod._login_attempts.clear()", False),
        ("import houndarr.auth as _auth_mod\n_auth_mod.reset_auth_caches()", False),
    ],
)
def test_rule_flags_planted_writes(source: str, flagged: bool) -> None:
    assert bool(shadowing_writes(source)) is flagged


def test_proxy_fixture_then_serializer_test_passes_in_order() -> None:
    """A proxy_settings test, then the two test_auth reads it once broke.

    Both test_auth tests failed whenever xdist ran them after a proxy-mode
    fixture on the same worker.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            "tests/test_proxy_auth.py::test_is_proxy_auth_mode_proxy",
            "tests/test_auth.py::test_get_serializer_lazy_init_reads_session_secret",
            "tests/test_auth.py::test_reset_auth_caches_clears_every_module_global",
        ],
        cwd=_REPO_ROOT,
        env=_ENV,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "3 passed" in result.stdout

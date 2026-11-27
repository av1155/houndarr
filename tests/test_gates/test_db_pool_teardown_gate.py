"""Consolidated invariant: a database test run on its own exits.

aiosqlite runs a non-daemon worker thread per connection, and the pool
registry in ``houndarr.database`` keeps those connections open until
``close_all_pools()`` runs.  A test that opens a pool without the ``db``
fixture and never closes it passes, then the interpreter waits on the
threads forever (#910).  A full run hides this whenever a later test
closes the pools, so each case runs one such test alone in a fresh
interpreter and requires a clean exit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.pinning

_REPO_ROOT = Path(__file__).resolve().parents[2]

# One test per file whose passing run leaves a pool open for the autouse teardown.
_NODES: tuple[str, ...] = (
    "tests/test_database.py::test_init_db_is_idempotent_on_healthy_v12",
    "tests/test_services/test_admin.py::test_factory_reset_deletes_files_and_reinits",
    "tests/test_bootstrap/test_bootstrap_non_web.py::TestReturnShape::test_returns_three_tuple",
)

# Without the teardown the child never exits; a passing run takes a few seconds.
_TIMEOUT_SECONDS = 60

# xdist and pytest set these in the parent run; the child is a standalone run.
_ENV = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}


@pytest.mark.parametrize("node", _NODES)
def test_lone_db_test_exits(node: str) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", node],
            cwd=_REPO_ROOT,
            env=_ENV,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{node} did not exit within {_TIMEOUT_SECONDS}s; a pool was left open")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout

"""Consolidated invariant: the runtime image keeps the legacy zone database.

``unresolved_timezone`` and the allowed-search-window tests cover the Python
side of issue #822, but the thing that actually fixes it is one package name
in the Dockerfile.  Nothing else in the suite can see it: the test job runs on
the runner's own zoneinfo rather than inside the image, the browser suite pins
``TZ=UTC``, and the smoke test sets no ``TZ`` at all.  Drop the package and
every required check still passes while ``TZ=US/Eastern`` silently goes back
to meaning UTC.

This gate is a tripwire, not a substitute for the behavioural check: it reads
the Dockerfile rather than the built image, so it catches the package being
removed, not Debian withdrawing it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"

# The apt invocation in the runtime stage, captured across its line
# continuations so a reformatted RUN block still matches.
_APT_INSTALL = re.compile(r"apt-get install[^\n]*(?:\\\n[^\n]*)*")


@pytest.fixture(scope="module")
def apt_install_line() -> str:
    """Return the runtime stage's apt-get install invocation."""
    match = _APT_INSTALL.search(_DOCKERFILE.read_text())
    assert match is not None, "no apt-get install found in the Dockerfile"
    return match.group(0)


def test_runtime_image_installs_the_legacy_timezone_package(apt_install_line: str) -> None:
    """Without tzdata-legacy, US/Eastern and GB resolve to UTC with no error.

    Debian split the backward-compatible zone names out of ``tzdata`` in
    2023c-8.  The C library opens no file for a name it cannot find and falls
    back to UTC silently, which inverts the allowed-search-window gate.
    """
    assert "tzdata-legacy" in apt_install_line


def test_legacy_timezone_package_is_not_a_recommends(apt_install_line: str) -> None:
    """The install uses --no-install-recommends, so the package must be explicit.

    Relying on it arriving as a recommendation of something else would put the
    fix at the mercy of an unrelated dependency change.
    """
    assert "--no-install-recommends" in apt_install_line

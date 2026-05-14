"""Pin the runtime Python-version backstop in ``houndarr.__main__``.

The package-metadata gate in ``pyproject.toml`` (``requires-python =
">=3.13"``) handles the normal install path, but a forced install or
a stale venv can still drop the operator into ``python -m houndarr``
on Python 3.12 or older.  ``_check_python_version`` is the friendly
stderr message + non-zero exit that closes that gap (issue #628).
"""

from __future__ import annotations

import pytest

from houndarr.__main__ import _check_python_version


def test_check_python_version_accepts_current_runtime() -> None:
    """Real interpreter is >=3.13; the bare call must be a no-op."""
    _check_python_version()


def test_check_python_version_accepts_future_versions() -> None:
    """A Python newer than the floor stays accepted (3.14, 4.0, ...)."""
    _check_python_version((3, 14))
    _check_python_version((3, 50))
    _check_python_version((4, 0))


def test_check_python_version_rejects_old(capsys: pytest.CaptureFixture[str]) -> None:
    """A 3.12 runtime writes the install hint and exits with code 2."""
    with pytest.raises(SystemExit) as exc:
        _check_python_version((3, 12))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Houndarr requires Python 3.13" in err
    assert "running Python 3.12" in err
    # The hint must mention at least one concrete install path so the
    # operator has something to copy-paste.
    assert "uv python install 3.13" in err
    assert "pyenv install 3.13" in err


def test_check_python_version_rejects_pre_3_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wildly old (2.x or pre-3.13) tuple still falls into the reject branch."""
    with pytest.raises(SystemExit) as exc:
        _check_python_version((2, 7))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "running Python 2.7" in err

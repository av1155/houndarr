"""Pin the compiled Tailwind bundle hash.

Track A.25 of the refactor plan.  Tracks E / F / G will modify the
templates and the CSS input files; any intentional change to the
compiled `app.built.css` must land together with an update to
`tests/_artifacts/app.built.css.sha256`.  This test is the guardrail
that forces the reference update to be explicit.

The test is a soft guard: it skips when the bundle is missing (dev
checkouts that have not run the Tailwind build).  CI runs the build
step in the Dockerfile's css-build stage, so the bundle is always
present when the test matters.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.pinning


_CSS_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "houndarr" / "static" / "css" / "app.built.css"
)
_REFERENCE_PATH = Path(__file__).resolve().parents[1] / "_artifacts" / "app.built.css.sha256"


def _read_reference_hash(path: Path) -> str:
    raw = path.read_text(encoding="utf-8").strip()
    # Format mirrors `sha256sum`: "<hex>  <path>".  Accept either form.
    return raw.split()[0]


class TestCssBundleHash:
    def test_reference_file_exists(self) -> None:
        assert _REFERENCE_PATH.is_file()

    def test_reference_format_is_64_hex_chars(self) -> None:
        reference = _read_reference_hash(_REFERENCE_PATH)
        assert len(reference) == 64
        int(reference, 16)  # must parse as hex

    def test_bundle_hash_matches_reference_when_present(self) -> None:
        """Skip if the compiled bundle is absent (Tailwind build not run)."""
        if not _CSS_PATH.is_file():
            pytest.skip("app.built.css not present; run the Tailwind build to enable")

        data = _CSS_PATH.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        reference = _read_reference_hash(_REFERENCE_PATH)
        assert actual == reference, (
            "app.built.css hash drift: update "
            "tests/_artifacts/app.built.css.sha256 only with a recorded rationale"
        )

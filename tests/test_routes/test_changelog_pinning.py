"""Pin the changelog route helper functions: bullet renderer and safe URLs.

The ``_render_changelog_bullet`` Jinja filter and ``_is_safe_url``
helper in ``routes/changelog.py`` implement a small, hand-rolled
markdown vocabulary (inline code, bold, ``[text](url)`` links,
``(#issue)`` refs).  They are the only place in the codebase where
user-authored changelog text turns into raw HTML, so they sit on a
trust boundary.

These pinning tests lock the exact output shape and the URL-scheme
allowlist so any edit to the helpers cannot silently change the
rendered HTML or widen the scheme surface.
"""

from __future__ import annotations

import pytest

from houndarr.routes.changelog import (
    _empty_slot_response,
    _is_safe_url,
    _range_label,
    _render_changelog_bullet,
)

pytestmark = pytest.mark.pinning


# _is_safe_url allowlist


class TestIsSafeUrl:
    """Pin the scheme allowlist and the schemeless path."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "https://example.com",
            "HTTPS://EXAMPLE.COM",  # case-insensitive
            "mailto:user@example.com",
            "/relative/path",
            "#fragment-only",
        ],
    )
    def test_allowed_schemes_accepted(self, url: str) -> None:
        assert _is_safe_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JAVASCRIPT:alert(1)",
            "data:text/html,<script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_hostile_schemes_rejected(self, url: str) -> None:
        assert _is_safe_url(url) is False

    def test_schemeless_relative_path_accepted(self) -> None:
        """A path without a colon has no scheme and is always safe."""
        assert _is_safe_url("some/relative/path") is True

    def test_schemeless_with_slash_before_colon_accepted(self) -> None:
        """If the first slash precedes the first colon, the colon is not a scheme delimiter.

        This handles the case where a relative URL happens to contain a colon
        in a later path segment (e.g. ``/page?title=foo:bar``).
        """
        assert _is_safe_url("/page?x=y:z") is True

    def test_whitespace_stripped_before_check(self) -> None:
        """Leading whitespace does not bypass the scheme allowlist."""
        assert _is_safe_url("   javascript:evil") is False
        assert _is_safe_url("   https://ok.example") is True


# _render_changelog_bullet output shape


class TestRenderChangelogBullet:
    """Pin the exact HTML emitted for each markdown vocabulary pattern."""

    def test_plain_text_is_escaped(self) -> None:
        """Ambient ``<`` and ``>`` are HTML-escaped."""
        out = str(_render_changelog_bullet("a < b & c > d"))
        assert "&lt;" in out
        assert "&gt;" in out
        assert "&amp;" in out
        assert "<script" not in out.lower()

    def test_inline_code_wrapped_in_code_tag(self) -> None:
        """Backtick-quoted text becomes ``<code class="text-brand-300">``."""
        out = str(_render_changelog_bullet("Use `ruff check` before commit."))
        assert '<code class="text-brand-300">ruff check</code>' in out

    def test_bold_wrapped_in_strong_tag(self) -> None:
        """Double-asterisk text becomes ``<strong>``."""
        out = str(_render_changelog_bullet("This is **bold** text."))
        assert "<strong>bold</strong>" in out

    def test_link_emits_anchor_with_safety_attrs(self) -> None:
        """``[text](url)`` with a safe URL becomes a hardened anchor."""
        out = str(_render_changelog_bullet("See [the docs](https://example.com/docs)."))
        assert 'href="https://example.com/docs"' in out
        assert 'target="_blank"' in out
        assert 'rel="noopener noreferrer"' in out
        assert ">the docs</a>" in out

    def test_link_with_unsafe_url_falls_back_to_raw_text(self) -> None:
        """A ``[text](javascript:...)`` link renders as the original markdown, not an anchor."""
        out = str(_render_changelog_bullet("Click [here](javascript:alert(1)) now."))
        assert "<a " not in out
        assert "javascript:alert(1)" in out
        assert "[here](" in out

    def test_issue_ref_wrapped_in_github_link(self) -> None:
        """``(#123)`` becomes a link to the GitHub issue tracker."""
        out = str(_render_changelog_bullet("Fixed the bug (#42)."))
        assert "av1155/houndarr/issues/42" in out
        assert ">#42</a>" in out
        assert 'rel="noopener noreferrer"' in out

    def test_links_with_nested_parentheses_render(self) -> None:
        """Wikipedia-style links with balanced inner parens render correctly."""
        out = str(_render_changelog_bullet("See [Bar](https://en.wikipedia.org/wiki/Foo_(bar))."))
        assert 'href="https://en.wikipedia.org/wiki/Foo_(bar)"' in out
        assert ">Bar</a>" in out

    def test_multiple_patterns_combine(self) -> None:
        """``**bold** with `code` and [link](url)`` all render in the same bullet."""
        raw = "**warning** use `ruff` per [docs](https://example.com) (#1)."
        out = str(_render_changelog_bullet(raw))
        assert "<strong>warning</strong>" in out
        assert '<code class="text-brand-300">ruff</code>' in out
        assert 'href="https://example.com"' in out
        assert ">docs</a>" in out
        assert "issues/1" in out


# _range_label


class TestRangeLabel:
    """Pin the modal subtitle logic."""

    def test_manual_open_returns_empty(self) -> None:
        """A manual open always suppresses the subtitle."""
        # Stub releases: list shape is enough, content is not inspected.
        releases = [object(), object()]  # two entries
        assert _range_label(releases, manual=True, last_seen="1.0.0") == ""  # type: ignore[arg-type]

    def test_single_release_suppresses_label(self) -> None:
        """If only one release is shown, the subtitle adds no information."""
        releases = [object()]
        assert _range_label(releases, manual=False, last_seen="1.0.0") == ""  # type: ignore[arg-type]

    def test_auto_open_with_last_seen_uses_since(self) -> None:
        """Auto-open with two+ releases and a last_seen value renders ``Since v<ver>``."""
        releases = [object(), object()]
        assert _range_label(releases, manual=False, last_seen="1.0.0") == "Since v1.0.0"  # type: ignore[arg-type]

    def test_auto_open_without_last_seen_empty(self) -> None:
        """Auto-open with no last_seen still renders empty (first-ever popup)."""
        releases = [object(), object()]
        assert _range_label(releases, manual=False, last_seen=None) == ""  # type: ignore[arg-type]


# _empty_slot_response shape


class TestEmptySlotResponse:
    """Pin the placeholder used when the popup decides not to render."""

    def test_returns_200(self) -> None:
        resp = _empty_slot_response()
        assert resp.status_code == 200

    def test_body_is_single_div_with_aria_hidden(self) -> None:
        resp = _empty_slot_response()
        body = resp.body.decode("utf-8")
        assert body == '<div id="changelog-slot" aria-hidden="true"></div>'

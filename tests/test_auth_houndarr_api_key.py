"""Tests for Houndarr API key token primitives."""

from __future__ import annotations

import base64
import re

import pytest

from houndarr.auth import houndarr_api_key


def test_generate_api_key_returns_prefixed_urlsafe_32_byte_token() -> None:
    """Generated tokens should carry the public prefix and 32 random bytes."""
    token = houndarr_api_key.generate_api_key()
    assert token.startswith("hndarr_")
    suffix = token.removeprefix("hndarr_")
    assert re.fullmatch(r"[A-Za-z0-9_-]+", suffix)
    padding = "=" * (-len(suffix) % 4)
    assert len(base64.urlsafe_b64decode(suffix + padding)) == 32


def test_hash_api_key_is_sha256_hex_digest() -> None:
    digest = houndarr_api_key.hash_api_key("sample-token")
    assert len(digest) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == houndarr_api_key.hash_api_key("sample-token")


def test_verify_api_key_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(houndarr_api_key, "compare_digest", fake_compare)
    token = "hndarr_example"
    stored_hash = houndarr_api_key.hash_api_key(token)

    assert houndarr_api_key.verify_api_key(token, stored_hash) is True
    assert calls == [(stored_hash, stored_hash)]


def test_verify_api_key_rejects_non_matching_token() -> None:
    stored_hash = houndarr_api_key.hash_api_key("hndarr_right")
    assert houndarr_api_key.verify_api_key("hndarr_wrong", stored_hash) is False

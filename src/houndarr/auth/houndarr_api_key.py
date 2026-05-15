"""Houndarr API key token generation and verification helpers."""

from __future__ import annotations

import hashlib
import secrets
from hmac import compare_digest

_TOKEN_PREFIX = "hndarr_"  # noqa: S105  # nosec B105 -- Public prefix, not a credential.
_TOKEN_BYTES = 32


def generate_api_key() -> str:
    """Generate a new plaintext Houndarr API key token."""
    return f"{_TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_api_key(token: str) -> str:
    """Return the SHA-256 hex digest for *token*."""
    # API keys are 256-bit random bearer tokens; SHA-256 stores a deterministic lookup digest.
    # codeql[py/weak-sensitive-data-hashing]
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_api_key(token: str, stored_hash: str) -> bool:
    """Return whether *token* matches *stored_hash* using constant-time comparison."""
    return compare_digest(hash_api_key(token), stored_hash)

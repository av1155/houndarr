"""Tests for the crypto module: master key management and Fernet encryption."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from houndarr.crypto import decrypt, encrypt, ensure_master_key

# ---------------------------------------------------------------------------
# ensure_master_key
# ---------------------------------------------------------------------------


def test_ensure_master_key_creates_file(tmp_path: Path) -> None:
    """First call should create the key file."""
    key_path = tmp_path / "houndarr.masterkey"
    assert not key_path.exists()
    ensure_master_key(tmp_path)
    assert key_path.exists()


def test_ensure_master_key_returns_valid_fernet_key(tmp_path: Path) -> None:
    """Returned key must be a valid Fernet key (44-byte base64)."""
    key = ensure_master_key(tmp_path)
    # Fernet will raise ValueError if the key is invalid
    Fernet(key)


def test_ensure_master_key_idempotent(tmp_path: Path) -> None:
    """Second call must return the *same* key as the first."""
    key1 = ensure_master_key(tmp_path)
    key2 = ensure_master_key(tmp_path)
    assert key1 == key2


def test_ensure_master_key_file_permissions(tmp_path: Path) -> None:
    """Key file must be created with mode 0o600."""
    ensure_master_key(tmp_path)
    key_path = tmp_path / "houndarr.masterkey"
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600


def test_ensure_master_key_str_path(tmp_path: Path) -> None:
    """ensure_master_key must accept a plain str as well as a Path."""
    key = ensure_master_key(str(tmp_path))
    Fernet(key)


# ---------------------------------------------------------------------------
# encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip() -> None:
    """Decrypting an encrypted value returns the original plaintext."""
    key = Fernet.generate_key()
    plaintext = "my-secret-api-key-1234"
    token = encrypt(plaintext, key)
    assert decrypt(token, key) == plaintext


def test_encrypt_returns_str() -> None:
    """encrypt() must return a str, not bytes."""
    key = Fernet.generate_key()
    result = encrypt("test", key)
    assert isinstance(result, str)


def test_decrypt_returns_str() -> None:
    """decrypt() must return a str, not bytes."""
    key = Fernet.generate_key()
    token = encrypt("test", key)
    result = decrypt(token, key)
    assert isinstance(result, str)


def test_encrypt_different_ciphertexts_for_same_input() -> None:
    """Two encrypt calls with the same input must produce different tokens (nonce)."""
    key = Fernet.generate_key()
    token1 = encrypt("same-value", key)
    token2 = encrypt("same-value", key)
    assert token1 != token2


def test_decrypt_wrong_key_raises_invalid_token() -> None:
    """Decrypting with a different key must raise InvalidToken."""
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    token = encrypt("secret", key1)
    with pytest.raises(InvalidToken):
        decrypt(token, key2)


def test_decrypt_tampered_token_raises_invalid_token() -> None:
    """Decrypting a corrupted token must raise InvalidToken."""
    key = Fernet.generate_key()
    token = encrypt("secret", key)
    # Flip one character near the end of the token
    tampered = token[:-4] + ("XXXX" if not token.endswith("XXXX") else "YYYY")
    with pytest.raises(InvalidToken):
        decrypt(tampered, key)


# ---------------------------------------------------------------------------
# Integration: master key is loaded into app.state
# ---------------------------------------------------------------------------


def test_app_state_has_master_key(app: object) -> None:
    """After lifespan startup, app.state.master_key must be a valid Fernet key."""
    # `app` fixture is a TestClient; the underlying app is app.app
    from fastapi.testclient import TestClient

    assert isinstance(app, TestClient)
    master_key = app.app.state.master_key  # type: ignore[attr-defined]
    assert isinstance(master_key, bytes)
    Fernet(master_key)

"""Master key management and Fernet-based encryption for API keys at rest."""

from __future__ import annotations

import os
import stat
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["InvalidToken", "MasterKeyError", "decrypt", "encrypt", "ensure_master_key"]


class MasterKeyError(RuntimeError):
    """Raised when ``houndarr.masterkey`` exists but holds no Fernet key."""


def ensure_master_key(data_dir: str | Path) -> bytes:
    """Return the Fernet master key for *data_dir*, creating it when the file is missing.

    The key is persisted to ``<data_dir>/houndarr.masterkey`` with mode
    ``0o600`` so that only the owning user can read it.  On subsequent calls
    the existing key is read and returned unchanged.  An existing file is
    never replaced: one that holds no valid key raises
    :class:`MasterKeyError`, since a new key would make every stored API key
    unreadable while the original might still be restored.

    Args:
        data_dir: Path to the application data directory (must already exist).

    Returns:
        32-byte URL-safe base64-encoded Fernet key.

    Raises:
        MasterKeyError: When the key file exists but is empty or damaged.
    """
    key_path = Path(data_dir) / "houndarr.masterkey"

    if key_path.exists():
        key = key_path.read_bytes().strip()
        try:
            _get_fernet(key)
        except ValueError as exc:
            raise MasterKeyError(
                f"The master key file '{key_path}' is damaged: it does not hold a "
                "valid key. Restore it from a backup, or delete it so Houndarr "
                "writes a new one and then enter every instance's API key again."
            ) from exc
        return key

    # Generate a brand-new key and write it atomically.
    key = Fernet.generate_key()

    # Write with O_CREAT | O_EXCL to avoid a TOCTOU race.
    try:
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write master key to '{key_path}'. "
            "The /data directory is not writable by the current user. "
            "If using Docker with PUID/PGID, ensure they match the ownership "
            "of your host data directory. "
            "If using 'user:' or 'runAsUser', ensure /data is pre-owned by "
            "that UID/GID. "
            "For Proxmox/LXC, set PUID=0/PGID=0."
        ) from exc
    # A partial file would be refused as damaged on every later start.
    try:
        try:
            if os.write(fd, key) != len(key):
                raise OSError(f"Short write to '{key_path}'")
        finally:
            os.close(fd)
    except BaseException:
        key_path.unlink(missing_ok=True)
        raise

    # Enforce 0o600 even if umask is permissive.
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    return key


@lru_cache(maxsize=4)
def _get_fernet(key: bytes) -> Fernet:
    """Return a cached Fernet instance for the given *key*."""
    return Fernet(key)


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt *plaintext* with the given Fernet *key*.

    Args:
        plaintext: The string to encrypt (e.g. an API key).
        key: A 32-byte URL-safe base64-encoded Fernet key.

    Returns:
        A URL-safe base64-encoded ciphertext token (str).
    """
    return _get_fernet(key).encrypt(plaintext.encode()).decode()


def decrypt(token: str, key: bytes) -> str:
    """Decrypt a Fernet *token* with the given *key*.

    Args:
        token: A URL-safe base64-encoded ciphertext token produced by
            :func:`encrypt`.
        key: The same Fernet key used to encrypt the token.

    Returns:
        The original plaintext string.

    Raises:
        cryptography.fernet.InvalidToken: If the key is wrong or the token
            has been tampered with / expired.
    """
    return _get_fernet(key).decrypt(token.encode()).decode()

"""Password hashing and verification helpers.

Thin bcrypt wrapper that the rest of the auth package and the route
layer compose over.  The cost factor is pinned at 12 so a future
rotation cannot silently weaken newly-hashed passwords; the
verification path is tolerant of malformed hashes because the
persisted ``password_hash`` setting can be corrupted in the wild.
"""

from __future__ import annotations

import bcrypt

BCRYPT_COST = 12


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt cost 12."""
    salt = bcrypt.gensalt(rounds=BCRYPT_COST)
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Return True if password matches the bcrypt hash.

    Every failure mode collapses to ``False`` so a corrupted
    ``password_hash`` setting cannot surface as a 500 at the login
    route.  bcrypt.checkpw raises ``ValueError`` on malformed hashes,
    ``UnicodeDecodeError`` on non-UTF8 content, ``TypeError`` on
    wrong argument types, and the modern Python bcrypt package also
    rejects >72-byte passwords with ``ValueError``; this catch-all
    covers every one of them.
    """
    try:
        return bool(bcrypt.checkpw(password.encode(), hashed.encode()))
    except Exception:  # noqa: BLE001
        return False

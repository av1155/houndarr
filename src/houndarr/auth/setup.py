"""First-run setup + credential management.

Houndarr supports exactly one admin account; this module owns every
piece of state that defines it:

- the one-time ``/setup`` gate controlled by ``is_setup_complete``,
- the ``password_hash`` and ``username`` settings persisted in SQLite,
- the ``check_credentials`` entry point the login route composes with
  the rate limiter and session creator,
- ``rotate_session_secret`` for the password-change flow, which
  invalidates every previously-signed cookie by asking the session
  seam to drop its cached serializer,
- ``reset_auth_caches`` for the factory-reset flow, which clears every
  module-level cache owned by the auth package.

The module is asynchronous-heavy because every SQLite read goes
through ``aiosqlite``; only ``normalize_username`` and
``validate_username`` are synchronous pure helpers.
"""

from __future__ import annotations

import os
import re
from hmac import compare_digest

from houndarr.auth import rate_limit as _rate_limit
from houndarr.auth import session as _session
from houndarr.auth.password import hash_password, verify_password
from houndarr.repositories.settings import get_setting, set_setting

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]+$")

_setup_complete: bool | None = None


async def is_setup_complete() -> bool:
    """Return True if the initial password has been set.

    The result is cached once True because the password_hash setting is
    monotonic: it transitions from absent to present and is never deleted.
    """
    global _setup_complete  # noqa: PLW0603
    if _setup_complete is True:
        return True
    result = (await get_setting("password_hash")) is not None
    if result:
        _setup_complete = True
    return result


async def set_password(password: str) -> None:
    """Hash and persist the application password."""
    global _setup_complete  # noqa: PLW0603
    await set_setting("password_hash", hash_password(password))
    _setup_complete = True


async def rotate_session_secret() -> None:
    """Regenerate the session signing secret and drop the serializer cache.

    Every cookie signed with the previous secret fails signature verification
    afterwards, so a stolen cookie cannot outlive a password change. The
    caller is responsible for issuing the current admin a fresh
    :func:`houndarr.auth.session.create_session` cookie on the outgoing
    response so the password change does not also sign them out of the tab
    they made it from.
    """
    await set_setting("session_secret", os.urandom(32).hex())
    _session.reset_serializer()


def normalize_username(username: str) -> str:
    """Return a normalized username for storage and comparison."""
    return username.strip().lower()


def validate_username(username: str) -> str | None:
    """Return an error message if username is invalid, else None."""
    normalized = normalize_username(username)
    if not normalized:
        return "Username is required."
    if len(normalized) < USERNAME_MIN_LENGTH or len(normalized) > USERNAME_MAX_LENGTH:
        return "Username must be 3-32 characters."
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        return "Username may only contain lowercase letters, numbers, dots, dashes, or underscores."
    return None


async def set_username(username: str) -> None:
    """Persist the normalized single-admin username."""
    await set_setting("username", normalize_username(username))


async def get_username() -> str | None:
    """Return the configured single-admin username."""
    return await get_setting("username")


async def check_password(password: str) -> bool:
    """Return True if password matches the stored hash."""
    stored = await get_setting("password_hash")
    if not stored:
        return False
    return verify_password(password, stored)


async def check_credentials(username: str, password: str) -> bool:
    """Return True if the provided username and password are valid.

    If a legacy install has a password hash but no username yet, the first
    successful password login claims the submitted username.
    """
    normalized_username = normalize_username(username)
    username_error = validate_username(normalized_username)
    if username_error is not None:
        return False

    if not await check_password(password):
        return False

    stored_username = await get_username()
    if stored_username is None:
        await set_username(normalized_username)
        return True

    return compare_digest(normalized_username, normalize_username(stored_username))


def reset_auth_caches() -> None:
    """Clear every module-level auth cache used by the builtin flow.

    Called by :func:`houndarr.services.admin.factory_reset` after the
    database is wiped so a subsequent ``/setup`` request is not short-
    circuited by a stale ``_setup_complete=True`` (or a serializer keyed
    to the old session_secret) and so brute-force counters start fresh.
    In proxy-mode installs these caches are not consulted for routing
    decisions, but resetting them keeps the module honest if the operator
    later switches modes.
    """
    global _setup_complete  # noqa: PLW0603
    _session.reset_serializer()
    _setup_complete = None
    _rate_limit.reset_login_attempts()

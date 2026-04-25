"""Authentication package: re-export shim for the auth seams.

The auth surface was deliberately split into submodules over seven
refactor commits (password, rate_limit, session, setup, csrf,
proxy_auth, identity, middleware).  This ``__init__.py`` is now a
pure re-export shim so every pre-split consumer import
(``from houndarr.auth import AuthMiddleware``, ``hash_password``,
``reset_auth_caches``, ``CSRF_COOKIE_NAME``, and so on) keeps
resolving to the same public names.

State-bearing module globals (``_serializer``, ``_setup_complete``,
``_USERNAME_PATTERN``) are resolved live through ``__getattr__``
because a ``from .submodule import _name`` binding would fork from
the authoritative value the submodule owns.
"""

from __future__ import annotations

# time is re-exported so tests that monkeypatch
# ``houndarr.auth.time.time`` continue to propagate the patch through
# to ``houndarr.auth.rate_limit``'s usage (the ``time`` module is a
# singleton, so patching via the auth namespace affects every
# importer).
import time  # noqa: F401
from typing import Any

from houndarr.auth import session as _session
from houndarr.auth import setup as _setup
from houndarr.auth.csrf import _CSRF_PROTECTED_METHODS, validate_csrf
from houndarr.auth.identity import resolve_signed_in_as
from houndarr.auth.middleware import _LOGOUT_PATH, _PUBLIC_PATHS, AuthMiddleware
from houndarr.auth.password import BCRYPT_COST, hash_password, verify_password
from houndarr.auth.proxy_auth import (
    _PROXY_DEAD_PATHS,
    _ensure_proxy_csrf_cookie,
    _extract_proxy_username,
    _is_proxy_auth_mode,
    _is_trusted_proxy,
    _validate_proxy_auth,
    _validate_proxy_csrf,
)
from houndarr.auth.rate_limit import (
    _LOGIN_MAX_ATTEMPTS,
    _LOGIN_WINDOW_SECONDS,
    _client_ip,
    _login_attempts,
    check_login_rate_limit,
    clear_login_attempts,
    record_failed_login,
    reset_login_attempts,
)
from houndarr.auth.session import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    _get_serializer,
    clear_session,
    create_session,
    get_session_csrf_token,
    validate_session,
)
from houndarr.auth.setup import (
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    check_credentials,
    check_password,
    get_username,
    is_setup_complete,
    normalize_username,
    reset_auth_caches,
    rotate_session_secret,
    set_password,
    set_username,
    validate_username,
)

__all__ = [
    "BCRYPT_COST",
    "CSRF_COOKIE_NAME",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE_SECONDS",
    "USERNAME_MAX_LENGTH",
    "USERNAME_MIN_LENGTH",
    "AuthMiddleware",
    "_CSRF_PROTECTED_METHODS",
    "_LOGIN_MAX_ATTEMPTS",
    "_LOGIN_WINDOW_SECONDS",
    "_LOGOUT_PATH",
    "_PROXY_DEAD_PATHS",
    "_PUBLIC_PATHS",
    "_client_ip",
    "_ensure_proxy_csrf_cookie",
    "_extract_proxy_username",
    "_get_serializer",
    "_is_proxy_auth_mode",
    "_is_trusted_proxy",
    "_login_attempts",
    "_validate_proxy_auth",
    "_validate_proxy_csrf",
    "check_credentials",
    "check_login_rate_limit",
    "check_password",
    "clear_login_attempts",
    "clear_session",
    "create_session",
    "get_session_csrf_token",
    "get_username",
    "hash_password",
    "is_setup_complete",
    "normalize_username",
    "record_failed_login",
    "reset_auth_caches",
    "reset_login_attempts",
    "resolve_signed_in_as",
    "rotate_session_secret",
    "set_password",
    "set_username",
    "validate_csrf",
    "validate_session",
    "validate_username",
    "verify_password",
]


def __getattr__(name: str) -> Any:
    """Resolve state-bearing globals live from their owning submodule.

    Package-level ``from X import Y`` binds Y at import time and misses
    later re-assignments inside the owning submodule.  For the globals
    tests and external callers inspect (``_serializer``,
    ``_setup_complete``, ``_USERNAME_PATTERN``), routing attribute
    access through ``__getattr__`` keeps ``houndarr.auth.<name>``
    showing the authoritative value every time.
    """
    if name == "_serializer":
        return _session._serializer
    if name == "_setup_complete":
        return _setup._setup_complete
    if name == "_USERNAME_PATTERN":
        return _setup._USERNAME_PATTERN
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Houndarr's exception hierarchy.

Previously the codebase had zero custom exceptions; error handling
relied on ``except Exception  # noqa: BLE001`` at 12+ sites.  This
module introduces a single root (``HoundarrError``) plus four layer-
specific branches so call sites can switch to named exceptions
incrementally in Tracks B.11-B.17.

The hierarchy is declaration-only in this batch; no raise site is
migrated yet.  Each concrete class documents which existing
``except Exception`` block it will eventually replace.
"""

from __future__ import annotations


class HoundarrError(Exception):
    """Root of every Houndarr-specific exception.

    Callers that want to distinguish Houndarr-originated errors from
    third-party exceptions (e.g. ``httpx.HTTPError``, ``aiosqlite.Error``)
    should catch this base.
    """


# ---------------------------------------------------------------------------
# Client-layer errors (clients/*.py)
# ---------------------------------------------------------------------------


class ClientError(HoundarrError):
    """Any failure raised from a ``*arr`` HTTP client."""


class ClientHTTPError(ClientError):
    """Non-2xx response from an ``*arr`` instance.

    Replaces ``httpx.HTTPStatusError`` bubble-ups at call sites that
    want to distinguish HTTP status failures from network errors.
    """


class ClientRedirectError(ClientHTTPError):
    """The *arr response redirected to a target blocked by SSRF rules.

    Raised by the ArrClient response event_hook when a 3xx response
    carries a ``Location`` header that resolves to a loopback,
    link-local, or unspecified address range.  Subclass of
    :class:`ClientHTTPError` so callers that handle broader HTTP
    status failures still catch redirects; callers that want redirect-
    specific telemetry can catch this class directly.
    """


class ClientTransportError(ClientError):
    """TCP / DNS / TLS failure talking to an ``*arr`` instance.

    Replaces ``httpx.TransportError`` bubble-ups at call sites that
    want to distinguish network failures from HTTP status failures.
    """


class ClientValidationError(ClientError):
    """The wire payload failed Pydantic validation.

    Replaces bare ``pydantic.ValidationError`` bubble-ups at call
    sites that want to attribute the failure to the wire boundary.
    """


class ClientUnreachableError(ClientError):
    """Catch-all for ``ArrClient.ping`` swallow-all failures.

    Currently ``ping()`` collapses four distinct errors
    (``httpx.HTTPError``, ``httpx.InvalidURL``, ``ValueError``,
    ``ValidationError``) to ``None``.  This class gives callers a
    typed way to re-raise the unreachable-state, for example from
    the supervisor's reconnect loop.
    """


# ---------------------------------------------------------------------------
# Engine-layer errors (engine/*.py)
# ---------------------------------------------------------------------------


class EngineError(HoundarrError):
    """Any failure raised inside the search engine pipeline."""


class EngineDispatchError(EngineError):
    """A search dispatch (adapter.dispatch_search) raised.

    Replaces ``except Exception`` at ``engine/search_loop.py:400-420``
    and ``:477-500`` (release-timing-retry and normal dispatch paths).
    """


class EnginePoolFetchError(EngineError):
    """fetch_upgrade_pool raised while building the upgrade candidate pool.

    Replaces ``except Exception`` at ``engine/search_loop.py:576``.
    """


class EngineOffsetPersistError(EngineError):
    """Persisting a ``*_page_offset`` / ``upgrade_item_offset`` failed.

    Replaces ``except Exception`` at ``engine/search_loop.py:608, 771,
    928, 971``.  Non-fatal (the next cycle retries); we log + continue.
    """


class EngineQueueProbeError(EngineError):
    """The queue-backpressure probe (``get_queue_status``) failed."""


# ---------------------------------------------------------------------------
# Service-layer errors (services/*.py, routes/admin.py)
# ---------------------------------------------------------------------------


class ServiceError(HoundarrError):
    """Any failure raised inside a Houndarr service."""


class InstanceValidationError(ServiceError):
    """An instance config failed service-level validation.

    Distinct from the form-level validators in
    ``routes/settings/_helpers.py``; those run before the service is
    called.
    """

    @property
    def public_message(self) -> str:
        """Curated user-facing message safe to surface in HTTP responses.

        Every raise site in this codebase constructs the exception with
        a single literal string argument (e.g. ``raise
        InstanceValidationError("Invalid instance type.")``).  Reading
        ``args[0]`` returns that literal verbatim, never the chained
        ``__cause__`` traceback that ``str(exc)`` could potentially
        leak in some Python builds.  Routes use this accessor so the
        guard banner cannot accidentally expose internal exception text
        even if a future raise site forgets to pass a curated string.
        """
        if not self.args:
            return ""
        return str(self.args[0])


class CooldownStateError(ServiceError):
    """Cooldown state is inconsistent (e.g. negative days).

    Defensive: the service should never raise this today, but adding
    it gives Track B.17 a target for the ``except Exception`` guard.
    """


class TimeWindowSpecError(ServiceError):
    """``allowed_time_window`` spec could not be parsed by the service.

    Mirrors ``parse_time_window`` raising ``ValueError``; wrapping
    into a typed service error lets callers distinguish it from
    other validation paths.
    """


# ---------------------------------------------------------------------------
# Route-layer errors (routes/*.py, auth.py)
# ---------------------------------------------------------------------------


class RouteError(HoundarrError):
    """Any failure raised inside a FastAPI route handler."""


class CsrfValidationError(RouteError):
    """CSRF validation failed for a mutating request."""


class AuthRejectedError(RouteError):
    """Authentication check rejected the current request."""

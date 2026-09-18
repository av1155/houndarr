"""Houndarr's exception hierarchy.

A single root (:class:`HoundarrError`) plus four layer-specific
branches (:class:`ClientError`, :class:`EngineError`,
:class:`ServiceError`, :class:`RouteError`) let call sites catch
Houndarr-originated failures by layer without rewrapping third-party
exceptions.  Each concrete subclass documents the surface it covers so
callers pick the narrowest useful base.
"""

from __future__ import annotations

import re

_URL_CREDENTIAL = re.compile(r"//([^/\s:@]+):[^/\s@]*@")


def redact_url_credentials(text: str) -> str:
    """Return *text* with any ``//user:password@`` password replaced.

    An operator fronting an \\*arr with basic auth puts the credential in
    the instance URL, and two things then carry it into a log: ``httpx``
    builds its ``HTTPStatusError`` message from ``str(request.url)``,
    which keeps userinfo where the URL's ``repr`` redacts it, and the
    reconnect rows interpolate ``instance.core.url`` directly.

    The username is left alone; only the secret is masked, following
    httpx's own ``repr`` convention.  Text carrying no such credential
    is returned byte-identical.
    """
    return _URL_CREDENTIAL.sub(r"//\1:[secure]@", text)


class HoundarrError(Exception):
    """Root of every Houndarr-specific exception.

    Callers that want to distinguish Houndarr-originated errors from
    third-party exceptions (e.g. ``httpx.HTTPError``, ``aiosqlite.Error``)
    should catch this base.
    """


# Client-layer errors (clients/*.py)


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


# Engine-layer errors (engine/*.py)


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


# Service-layer errors (services/*.py, routes/admin.py)


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
        """Return the curated user-facing string safe to surface in HTTP responses.

        Every raise site in this codebase constructs the exception with
        a single literal string argument (e.g. ``raise
        InstanceValidationError("Invalid instance type.")``).  Reading
        ``args[0]`` returns that literal verbatim regardless of how
        many positional args were passed; ``str(exc)`` only matches
        ``args[0]`` for the single-arg case and otherwise coerces the
        whole tuple to ``repr(args)`` (e.g. ``"('a', 'b')"``).  Routes
        use this accessor so the guard banner shows the curated
        message even if a future raise site forgets the convention
        and passes multiple positional args.

        Returns:
            ``str(args[0])`` when the exception was constructed with
            at least one argument; the empty string otherwise.
        """
        if not self.args:
            return ""
        return str(self.args[0])


class CooldownStateError(ServiceError):
    """Cooldown state is inconsistent (e.g. negative days).

    Defensive: the service should never raise this today.  Keeping
    the class lets the service-wide ``except Exception`` guard narrow
    to a named subclass without losing coverage.
    """


class TimeWindowSpecError(ServiceError):
    """``allowed_time_window`` spec could not be parsed by the service.

    Mirrors ``parse_time_window`` raising ``ValueError``; wrapping
    into a typed service error lets callers distinguish it from
    other validation paths.
    """


# Route-layer errors (routes/*.py, auth.py)


class RouteError(HoundarrError):
    """Any failure raised inside a FastAPI route handler."""


class CsrfValidationError(RouteError):
    """CSRF validation failed for a mutating request."""


class AuthRejectedError(RouteError):
    """Authentication check rejected the current request."""


def describe_exception(exc: BaseException) -> str:
    """Return an exception's message, or its type name when it has none.

    ``httpx`` maps transport timeouts from a bare ``TimeoutError()``, so
    ``httpx.ReadTimeout`` and ``httpx.ConnectTimeout`` stringify to the
    empty string.  Interpolating one leaves a log line that stops at the
    colon, exactly when an operator needs to know whether the \\*arr timed
    out, refused the connection, or answered with something unparseable.

    A credential embedded in a URL is redacted, since the message is
    stored in ``search_log`` and rendered on the Logs page.  Messages
    carrying no such credential are returned byte-identical.
    """
    return redact_url_credentials(str(exc)) or type(exc).__name__

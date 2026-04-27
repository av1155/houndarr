"""Abstract base class for *arr API clients."""

from __future__ import annotations

import ipaddress
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from houndarr.clients._wire_models import PaginatedResponse, QueueStatus, SystemStatus
from houndarr.errors import (
    ClientError,
    ClientHTTPError,
    ClientRedirectError,
    ClientTransportError,
    ClientValidationError,
)
from houndarr.services.url_validation import is_blocked_address

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InstanceSnapshot:
    """Per-instance library counts surfaced on the dashboard.

    ``monitored_total`` is the total monitored missing plus cutoff-unmet
    library size.  ``unreleased_count`` is the subset of monitored items
    whose release date is in the future (pre-release).  Written to the
    ``instances`` table by the supervisor's refresh task at the
    configured cadence; read by ``/api/status`` per request.
    """

    monitored_total: int
    unreleased_count: int


@dataclass(frozen=True, slots=True)
class ReconcileSets:
    """Authoritative (item_type, item_id) sets for each search pass.

    One set per ``search_kind`` value stamped on cooldown rows.  Each
    entry is a pair that matches a cooldown row's ``(item_type,
    item_id)`` columns: leaf item ids for straight-episode / movie /
    album / book / scene searches, or the negative synthetic ids
    (e.g. ``-(series_id * 1000 + season_number)``) that context-mode
    adapters stamp when the search was dispatched at the parent
    level.  A cooldown row is considered live iff its ``(item_type,
    item_id)`` appears in the matching ``search_kind`` set.

    The adapter populates both leaves AND synthetics in one pass:
    synthetics are derived from the leaf wanted list by grouping on
    the parent id carried on each item (e.g. ``series_id``,
    ``artist_id``, ``author_id``).  Reconciliation stays a pure set
    membership check and does not need adapter-specific logic.
    """

    missing: frozenset[tuple[str, int]]
    cutoff: frozenset[tuple[str, int]]
    upgrade: frozenset[tuple[str, int]]

    @classmethod
    def empty(cls) -> ReconcileSets:
        """Return a :class:`ReconcileSets` with three empty sets.

        Used when an adapter cannot build a reliable authoritative set
        (network failure mid-fetch, unsupported feature path) so the
        supervisor skips reconciliation for that cycle rather than
        wiping every cooldown row against an empty reference.
        """
        return cls(missing=frozenset(), cutoff=frozenset(), upgrade=frozenset())

    def is_empty(self) -> bool:
        """Return True if every pass set is empty.

        The reconciler treats an all-empty result as an explicit
        signal to SKIP the DELETE step: an instance that genuinely has
        zero wanted items and zero upgrade-pool items will not have
        cooldowns either, so the no-op is safe; and a mid-fetch
        failure that returned :meth:`empty` must never drive the DB
        to a blank state.
        """
        return not self.missing and not self.cutoff and not self.upgrade


WantedKind = Literal["missing", "cutoff"]


# Default timeouts (seconds): connect=5, read=30
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# Exceptions :meth:`ArrClient.ping` collapses to ``None``.  The contract
# is "never raise on a ping failure"; this tuple enumerates every known
# failure mode (transport, malformed URL, JSON-parse, schema-mismatch,
# and any ``ClientError`` raised by the response event_hook such as
# ``ClientRedirectError`` when a 3xx ``Location`` targets a blocked
# address range).  Callers that need a typed escalation wrap the
# ``None`` return themselves.
_PING_SAFE_ERRORS: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    httpx.InvalidURL,
    ValueError,
    ValidationError,
    ClientError,
)

# HTTP status codes that carry a redirect target in the Location header.
# The AsyncClient is configured with ``follow_redirects=False`` so httpx
# surfaces these as a normal response.  The response event_hook below
# re-validates the Location target against the SSRF guard as defense-in-
# depth against a future accidental ``follow_redirects=True`` flip.
_REDIRECT_STATUS_CODES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


async def _redirect_guard(response: httpx.Response) -> None:
    """Reject redirect targets that resolve to a blocked address range.

    Invoked by httpx on every response (``event_hooks["response"]``).
    Only 3xx responses with a ``Location`` header are inspected; relative
    locations are treated as same-host (the *arr URL already validated)
    and left alone.  Absolute targets are parsed and the host (IP
    literal or resolved hostname) is checked via
    :func:`houndarr.services.url_validation.is_blocked_address`.

    Raises:
        ClientRedirectError: The Location header names a blocked target.
    """
    if response.status_code not in _REDIRECT_STATUS_CODES:
        return
    location = response.headers.get("Location", "").strip()
    if not location:
        return
    # Parse the Location; urlparse tolerates scheme-relative URLs via the
    # `scheme=` fallback.  A relative location (no scheme, no netloc) is
    # same-host and inherits the *arr URL we already validated at
    # connect time, so nothing to check.
    parsed = urlparse(location)
    if not parsed.scheme and not parsed.netloc:
        return
    host = parsed.hostname or ""
    if not host:
        return
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Hostname: hand off to the DNS-resolving validator so any A / AAAA
        # record that resolves to a blocked range trips the guard.  We
        # import late to avoid a circular import during module load.
        from houndarr.services.url_validation import validate_instance_url

        error = validate_instance_url(location)
        if error is not None:
            raise ClientRedirectError(
                f"refusing redirect to blocked target {location!r}: {error}"
            ) from None
        return
    if is_blocked_address(addr):
        raise ClientRedirectError(f"refusing redirect to blocked target {location!r}") from None


class ArrClient(ABC):
    """Thin async wrapper around an *arr REST API.

    Subclasses implement :meth:`get_missing`, :meth:`get_cutoff_unmet`, and
    :meth:`search` for their specific resource type.

    Override :attr:`_SYSTEM_STATUS_PATH` for apps whose API version differs
    from the v3 default (e.g. Lidarr and Readarr use ``/api/v1/``).

    Subclasses with ``/wanted`` endpoints (every paginated client today
    apart from Whisparr v3) override the four ``_WANTED_*`` class-level
    hooks and let :meth:`_fetch_wanted_page` and :meth:`_fetch_wanted_total`
    do the request shaping, validation, and typed-error wrap.

    Usage::

        async with SonarrClient(url="http://sonarr:8989", api_key="abc") as client:
            missing = await client.get_missing(page=1, page_size=10)

    The client can also be used without the context manager if the caller
    manages the lifecycle of the underlying :class:`httpx.AsyncClient`.
    """

    _SYSTEM_STATUS_PATH: str = "/api/v3/system/status"
    _QUEUE_STATUS_PATH: str = "/api/v3/queue/status"

    # Class-level hooks for the /wanted template.  Subclasses with a /wanted
    # endpoint override these; Whisparr v3 leaves them unset because it
    # computes totals from /api/v3/movie instead.  See _fetch_wanted_page
    # for the per-hook contract.
    _WANTED_BASE_PATH: ClassVar[str] = "/api/v3/wanted"
    _WANTED_SORT_KEY: ClassVar[str] = ""
    _WANTED_INCLUDE_PARAM: ClassVar[str | None] = None
    _WANTED_ENVELOPE: ClassVar[type[PaginatedResponse[Any]] | None] = None

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> None:
        base = url.rstrip("/")
        # SSRF posture: redirects never followed at the client level; see
        # services/url_validation.py threat model.  ``follow_redirects``
        # is stated explicitly (httpx's own default is False) so a
        # future dependency upgrade that flips the default cannot
        # silently weaken the posture.  The response event_hook below
        # is the defense-in-depth layer: even if follow_redirects ever
        # goes True, any 3xx ``Location`` that resolves to a blocked
        # target (loopback, link-local, unspecified) raises before the
        # transport would chase it.
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=False,
            event_hooks={"response": [_redirect_guard]},
        )

    # Context-manager support

    async def __aenter__(self) -> ArrClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.__aexit__(*args)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # Low-level helpers

    async def _get(self, path: str, **params: Any) -> Any:
        """GET *path* with optional query *params*, raise on non-2xx."""
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, json: Any = None) -> Any:
        """POST *path* with optional JSON body, raise on non-2xx."""
        response = await self._client.post(path, json=json)
        response.raise_for_status()
        return response.json()

    # Health check

    async def ping(self) -> SystemStatus | None:
        """Return the parsed system status if reachable, or ``None``.

        Uses the system/status endpoint at :attr:`_SYSTEM_STATUS_PATH`.
        Defaults to ``/api/v3/system/status`` (Radarr, Sonarr, Whisparr);
        Lidarr and Readarr override to ``/api/v1/system/status``.

        The returned :class:`SystemStatus` exposes ``app_name`` and
        ``version``; both are optional because *arr forks sometimes omit
        them.  Network failures and malformed payloads both collapse to
        ``None`` so callers can treat unreachable and unparseable alike.
        Callers that need a typed escalation of the unreachable state
        (for example the supervisor's reconnect loop) wrap the ``None``
        return in :class:`~houndarr.errors.ClientUnreachableError`
        themselves; this method intentionally does not raise.
        """
        try:
            result = await self._get(self._SYSTEM_STATUS_PATH)
            return SystemStatus.model_validate(result)
        except _PING_SAFE_ERRORS:
            return None

    # Queue status

    async def get_queue_status(self) -> QueueStatus:
        """Fetch the download queue status from the *arr instance.

        Returns a :class:`QueueStatus` with ``total_count``: the total
        number of items currently in the download queue.  All five *arr
        apps expose the same ``QueueStatusResource`` schema here.

        Uses :attr:`_QUEUE_STATUS_PATH` which defaults to
        ``/api/v3/queue/status`` (Sonarr, Radarr, Whisparr) and is overridden
        to ``/api/v1/queue/status`` by Lidarr and Readarr.

        Raw ``httpx`` and ``pydantic`` failures are wrapped in typed
        :class:`~houndarr.errors.ClientError` subclasses so callers get
        a Houndarr-specific surface they can catch.  The original
        exception is preserved on ``__cause__`` via ``raise ... from``.

        Raises:
            ClientHTTPError: The server returned a non-2xx status.
            ClientTransportError: The request failed before a response
                arrived (connection refused, DNS failure, timeout,
                malformed URL, etc.).
            ClientValidationError: The response parsed as JSON but did
                not match the :class:`QueueStatus` schema.
        """
        try:
            result = await self._get(self._QUEUE_STATUS_PATH)
        except httpx.HTTPStatusError as exc:
            raise ClientHTTPError(
                f"queue status: HTTP {exc.response.status_code} from {self._QUEUE_STATUS_PATH}"
            ) from exc
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            raise ClientTransportError(
                f"queue status: transport error reaching {self._QUEUE_STATUS_PATH}: {exc}"
            ) from exc

        try:
            return QueueStatus.model_validate(result)
        except ValidationError as exc:
            raise ClientValidationError(
                f"queue status: malformed payload from {self._QUEUE_STATUS_PATH}"
            ) from exc

    # /wanted template (paginated clients only; Whisparr v3 does not use it)

    async def _fetch_wanted_page(
        self,
        kind: WantedKind,
        *,
        page: int,
        page_size: int,
        include_sort: bool = True,
        include_param: bool = True,
    ) -> PaginatedResponse[Any]:
        """Fetch one page of ``/wanted/{kind}`` via the per-subclass envelope.

        The four class-level hooks (override at subclass scope):

        - :attr:`_WANTED_BASE_PATH`: API root that prefixes ``/{kind}``.
          Defaults to ``/api/v3/wanted`` for v3-API clients (Sonarr, Radarr,
          Whisparr v2); Lidarr and Readarr override to ``/api/v1/wanted``.
        - :attr:`_WANTED_SORT_KEY`: sort field name passed when ``include_sort``
          is true.  Required for every paginated /wanted client; the missing
          pass on every client and the cutoff pass on Radarr include it.
        - :attr:`_WANTED_INCLUDE_PARAM`: optional embed-parent query param
          (``includeSeries`` for Sonarr / Whisparr v2, ``includeArtist`` for
          Lidarr, ``includeAuthor`` for Readarr, ``None`` for Radarr).
        - :attr:`_WANTED_ENVELOPE`: the parametrised :class:`PaginatedResponse`
          subclass (e.g. ``PaginatedResponse[RadarrWantedMovie]``) that
          validates the response payload.

        ``include_sort`` defaults to ``True`` because the missing pass on
        every paginated client sorts ascending by its release-date field.
        Callers pass ``include_sort=False`` for the cutoff pass on Sonarr,
        Lidarr, Readarr, and Whisparr v2 because those endpoints today omit
        the sort params; Radarr's cutoff endpoint includes them so it
        relies on the default.

        ``include_param`` defaults to ``True`` because the page reads on
        every embed-using client want the parent aggregate inlined into
        each record (so the parser can read ``series.title`` /
        ``artist.artistName`` / ``author.authorName`` without an extra
        round trip).  :meth:`_fetch_wanted_total` flips it to ``False``
        because the size-1 probe only needs ``totalRecords`` and
        omitting the embed keeps that probe as cheap as the *arr APIs
        allow.

        Subclasses without ``/wanted`` endpoints (Whisparr v3) leave the
        hooks unset and never call this method; calling it on a client
        whose :attr:`_WANTED_ENVELOPE` is unset raises
        :class:`NotImplementedError` so the misconfiguration surfaces
        immediately rather than silently producing an empty result.

        Raw ``httpx`` and ``pydantic`` errors propagate unwrapped so
        callers can choose: page-level reads (``get_missing``,
        ``get_cutoff_unmet``) re-raise the raw exception today; the
        size-1 probe in :meth:`_fetch_wanted_total` wraps them into
        typed :class:`~houndarr.errors.ClientError` subclasses.

        Args:
            kind: ``"missing"`` or ``"cutoff"``.
            page: 1-based page number.
            page_size: Number of records to request.
            include_sort: When true, append ``sortKey`` and
                ``sortDirection`` to the request.
            include_param: When true and :attr:`_WANTED_INCLUDE_PARAM` is
                set, append ``{_WANTED_INCLUDE_PARAM}=true`` to the
                request.

        Returns:
            The parsed :class:`PaginatedResponse` envelope.  Records are
            typed ``Any`` at the static-typing layer because the envelope
            class is supplied at the class-attribute level; runtime
            records are the per-app wire model declared in
            :attr:`_WANTED_ENVELOPE`.

        Raises:
            NotImplementedError: :attr:`_WANTED_ENVELOPE` is unset on the
                concrete subclass.
            httpx.HTTPError: Non-2xx response or transport failure.
            pydantic.ValidationError: Response payload did not match the
                declared envelope schema.
        """
        envelope_cls = type(self)._WANTED_ENVELOPE
        if envelope_cls is None:
            raise NotImplementedError(
                f"{type(self).__name__} did not declare _WANTED_ENVELOPE; "
                "the /wanted template requires a per-subclass envelope."
            )
        path = f"{self._WANTED_BASE_PATH}/{kind}"
        params: dict[str, Any] = {
            "page": page,
            "pageSize": page_size,
            "monitored": "true",
        }
        if include_sort:
            params["sortKey"] = self._WANTED_SORT_KEY
            params["sortDirection"] = "ascending"
        if include_param and self._WANTED_INCLUDE_PARAM is not None:
            params[self._WANTED_INCLUDE_PARAM] = "true"
        data = await self._get(path, **params)
        return envelope_cls.model_validate(data)

    async def _fetch_wanted_total(self, kind: WantedKind) -> int:
        """Default size-1 probe for ``/wanted/{kind}`` totals with typed wraps.

        Subclasses with ``/wanted`` endpoints rely on this default and let
        :meth:`get_wanted_total` collapse to a one-liner.  Whisparr v3
        overrides :meth:`get_wanted_total` directly because it has no
        ``/wanted`` path and computes the total from a cached
        ``/api/v3/movie`` response.

        Wraps raw ``httpx`` and ``pydantic`` failures into typed
        :class:`~houndarr.errors.ClientError` subclasses so callers get
        a Houndarr-specific surface they can catch.  The original
        exception is preserved on ``__cause__`` via ``raise ... from``.

        Raises:
            ClientHTTPError: Non-2xx response.
            ClientTransportError: Transport failure (connect, timeout,
                malformed URL, etc.).
            ClientValidationError: Response shape did not match the
                paginated envelope schema.
        """
        path = f"{self._WANTED_BASE_PATH}/{kind}"
        try:
            # The probe omits the embed-parent param because
            # ``totalRecords`` is independent of record shape; keeping
            # the probe payload minimal lowers the cost of the probe
            # against the *arr APIs.
            envelope = await self._fetch_wanted_page(
                kind,
                page=1,
                page_size=1,
                include_param=False,
            )
        except httpx.HTTPStatusError as exc:
            raise ClientHTTPError(
                f"wanted total: HTTP {exc.response.status_code} from {path}"
            ) from exc
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            raise ClientTransportError(
                f"wanted total: transport error reaching {path}: {exc}"
            ) from exc
        except ValidationError as exc:
            raise ClientValidationError(f"wanted total: malformed payload from {path}") from exc
        return envelope.total_records

    # Abstract interface

    @abstractmethod
    async def get_missing(self, *, page: int = 1, page_size: int = 10) -> list[Any]:
        """Return a page of missing items from the *arr instance."""

    @abstractmethod
    async def get_cutoff_unmet(self, *, page: int = 1, page_size: int = 10) -> list[Any]:
        """Return a page of cutoff-unmet items from the *arr instance."""

    @abstractmethod
    async def search(self, item_id: int) -> None:
        """Trigger an automatic search for the item identified by *item_id*."""

    @abstractmethod
    async def get_wanted_total(self, kind: WantedKind) -> int:
        """Return the total number of records in the wanted/*kind* list.

        Used by the engine's random-start-page computation to size the
        random page range.  Implementations should use the cheapest available
        probe (``pageSize=1`` for paged APIs; cached counts for Whisparr v3).
        """

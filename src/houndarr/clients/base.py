"""Abstract base class for *arr API clients."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from houndarr.clients._wire_models import QueueStatus, SystemStatus

logger = logging.getLogger(__name__)

# Default timeouts (seconds): connect=5, read=30
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# Exceptions :meth:`ArrClient.ping` collapses to ``None``.  The contract
# is "never raise on a ping failure"; this tuple enumerates every known
# failure mode (transport, malformed URL, JSON-parse, schema-mismatch).
# Callers that need a typed escalation wrap the ``None`` return
# themselves.
_PING_SAFE_ERRORS: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    httpx.InvalidURL,
    ValueError,
    ValidationError,
)


class ArrClient(ABC):
    """Thin async wrapper around an *arr REST API.

    Subclasses implement :meth:`get_missing`, :meth:`get_cutoff_unmet`, and
    :meth:`search` for their specific resource type.

    Override :attr:`_SYSTEM_STATUS_PATH` for apps whose API version differs
    from the v3 default (e.g. Lidarr and Readarr use ``/api/v1/``).

    Usage::

        async with SonarrClient(url="http://sonarr:8989", api_key="abc") as client:
            missing = await client.get_missing(page=1, page_size=10)

    The client can also be used without the context manager if the caller
    manages the lifecycle of the underlying :class:`httpx.AsyncClient`.
    """

    _SYSTEM_STATUS_PATH: str = "/api/v3/system/status"
    _QUEUE_STATUS_PATH: str = "/api/v3/queue/status"

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> None:
        base = url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> ArrClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.__aexit__(*args)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def ping(self) -> SystemStatus | None:
        """Return the parsed system status if reachable, or ``None``.

        Uses the system/status endpoint at :attr:`_SYSTEM_STATUS_PATH`.
        Defaults to ``/api/v3/system/status`` (Radarr, Sonarr, Whisparr);
        Lidarr and Readarr override to ``/api/v1/system/status``.

        The returned :class:`SystemStatus` exposes ``app_name`` and
        ``version``; both are optional because *arr forks sometimes omit
        them.  Network failures and malformed payloads both collapse to
        ``None`` so callers can treat unreachable and unparseable alike.
        """
        try:
            result = await self._get(self._SYSTEM_STATUS_PATH)
            return SystemStatus.model_validate(result)
        except _PING_SAFE_ERRORS:
            return None

    # ------------------------------------------------------------------
    # Queue status
    # ------------------------------------------------------------------

    async def get_queue_status(self) -> QueueStatus:
        """Fetch the download queue status from the *arr instance.

        Returns a :class:`QueueStatus` with ``total_count``: the total
        number of items currently in the download queue.  All five *arr
        apps expose the same ``QueueStatusResource`` schema here.

        Uses :attr:`_QUEUE_STATUS_PATH` which defaults to
        ``/api/v3/queue/status`` (Sonarr, Radarr, Whisparr) and is overridden
        to ``/api/v1/queue/status`` by Lidarr and Readarr.

        Raises:
            httpx.HTTPError: If the request fails or returns a non-2xx status.
            pydantic.ValidationError: If the response is missing
                ``totalCount`` or its shape cannot be validated.
        """
        result = await self._get(self._QUEUE_STATUS_PATH)
        return QueueStatus.model_validate(result)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

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
    async def get_wanted_total(self, kind: Literal["missing", "cutoff"]) -> int:
        """Return the total number of records in the wanted/*kind* list.

        Used by the engine's random-start-page computation to size the
        random page range.  Implementations should use the cheapest available
        probe (``pageSize=1`` for paged APIs; cached counts for Whisparr v3).
        """

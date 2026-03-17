"""Abstract base class for *arr API clients."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default timeouts (seconds): connect=5, read=30
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


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

    async def ping(self) -> bool:
        """Return ``True`` if the instance is reachable and healthy.

        Uses the system/status endpoint at :attr:`_SYSTEM_STATUS_PATH`.
        Defaults to ``/api/v3/system/status`` (Sonarr, Radarr, Whisparr);
        Lidarr and Readarr override to ``/api/v1/system/status``.
        """
        try:
            await self._get(self._SYSTEM_STATUS_PATH)
            return True
        except (httpx.HTTPError, httpx.InvalidURL):
            return False

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

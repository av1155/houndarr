"""Readarr v1 API client — missing books and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from houndarr.clients.base import ArrClient

__all__ = ["MissingBook", "ReadarrClient"]


@dataclass(frozen=True)
class MissingBook:
    """A single missing book returned by Readarr's wanted/missing endpoint."""

    book_id: int
    author_id: int
    author_name: str
    title: str
    release_date: str | None  # ISO-8601 nullable string


class ReadarrClient(ArrClient):
    """Async client for the Readarr v1 REST API."""

    _SYSTEM_STATUS_PATH: str = "/api/v1/system/status"

    async def get_missing(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingBook]:
        """Return a page of monitored missing books.

        Calls ``GET /api/v1/wanted/missing`` with ``includeAuthor=true``
        so that author metadata is embedded in each record.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingBook` dataclasses.
        """
        data: dict[str, Any] = await self._get(
            "/api/v1/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="releaseDate",
            sortDirection="ascending",
            includeAuthor="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_book(r) for r in records]

    async def search(self, item_id: int) -> None:
        """Trigger an automatic book search in Readarr.

        Calls ``POST /api/v1/command`` with command ``BookSearch``.

        Args:
            item_id: Readarr book ID to search for.
        """
        await self._post(
            "/api/v1/command",
            json={"name": "BookSearch", "bookIds": [item_id]},
        )

    async def search_author(self, author_id: int) -> None:
        """Trigger an author-context search in Readarr.

        Calls ``POST /api/v1/command`` with command ``AuthorSearch``.

        Args:
            author_id: Readarr author ID.
        """
        await self._post(
            "/api/v1/command",
            json={"name": "AuthorSearch", "authorId": author_id},
        )

    async def get_cutoff_unmet(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> list[MissingBook]:
        """Return a page of monitored books that have not met their quality cutoff.

        Calls ``GET /api/v1/wanted/cutoff`` with ``includeAuthor=true``.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingBook` dataclasses.
        """
        data: dict[str, Any] = await self._get(
            "/api/v1/wanted/cutoff",
            page=page,
            pageSize=page_size,
            includeAuthor="true",
            monitored="true",
        )
        records: list[dict[str, Any]] = data.get("records", [])
        return [_parse_book(r) for r in records]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_book(record: dict[str, Any]) -> MissingBook:
    author: dict[str, Any] = record.get("author") or {}
    return MissingBook(
        book_id=record["id"],
        author_id=record.get("authorId") or author.get("id") or 0,
        author_name=author.get("authorName") or "",
        title=record.get("title") or "",
        release_date=record.get("releaseDate"),
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_readarr_client(
    url: str,
    api_key: str,
    timeout: httpx.Timeout = httpx.Timeout(30.0, connect=5.0),
) -> ReadarrClient:
    """Return a :class:`ReadarrClient` ready for use as an async context manager."""
    return ReadarrClient(url=url, api_key=api_key, timeout=timeout)

"""Readarr v1 API client: missing books and automatic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from houndarr.clients._wire_models import (
    PaginatedResponse,
    ReadarrLibraryBook,
    ReadarrWantedBook,
)
from houndarr.clients.base import ArrClient, WantedKind

__all__ = ["LibraryBook", "MissingBook", "ReadarrClient"]


@dataclass(frozen=True, slots=True)
class LibraryBook:
    """A book from Readarr's full library endpoint."""

    book_id: int
    author_id: int
    author_name: str
    title: str
    monitored: bool
    has_file: bool
    release_date: str | None


@dataclass(frozen=True, slots=True)
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
    _QUEUE_STATUS_PATH: str = "/api/v1/queue/status"
    # Readarr is a v1 API; the override routes the /wanted template at
    # /api/v1/wanted/{kind} (matches Lidarr's pattern).
    _WANTED_BASE_PATH: ClassVar[str] = "/api/v1/wanted"
    _WANTED_SORT_KEY: ClassVar[str] = "releaseDate"
    _WANTED_INCLUDE_PARAM: ClassVar[str | None] = "includeAuthor"
    _WANTED_ENVELOPE: ClassVar[type[PaginatedResponse[ReadarrWantedBook]]] = PaginatedResponse[
        ReadarrWantedBook
    ]

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
        envelope = await self._fetch_wanted_page("missing", page=page, page_size=page_size)
        return [_parse_book(w) for w in envelope.records]

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
        Readarr's cutoff endpoint historically omits the sort params, so
        the call passes ``include_sort=False`` to suppress them.

        Args:
            page: 1-based page number.
            page_size: Number of records per page.

        Returns:
            List of :class:`MissingBook` dataclasses.
        """
        envelope = await self._fetch_wanted_page(
            "cutoff",
            page=page,
            page_size=page_size,
            include_sort=False,
        )
        return [_parse_book(w) for w in envelope.records]

    async def get_wanted_total(self, kind: WantedKind) -> int:
        """Return the totalRecords count for ``wanted/{kind}`` via a size-1 probe.

        Delegates to :meth:`ArrClient._fetch_wanted_total`, which wraps
        raw ``httpx`` and ``pydantic`` failures in typed
        :class:`~houndarr.errors.ClientError` subclasses with the
        original exception preserved on ``__cause__``.

        Raises:
            ClientHTTPError: Non-2xx response.
            ClientTransportError: Transport failure (connect, timeout,
                malformed URL, etc.).
            ClientValidationError: Response shape did not match the
                paginated envelope schema.
        """
        return await self._fetch_wanted_total(kind)

    async def get_books(self) -> list[LibraryBook]:
        """Return the full book library.

        Calls ``GET /api/v1/book`` with ``includeAuthor=true``.

        Returns:
            List of :class:`LibraryBook` dataclasses.
        """
        records = await self._get(
            "/api/v1/book",
            includeAuthor="true",
        )
        return [_parse_library_book(ReadarrLibraryBook.model_validate(r)) for r in records]


# Parsing helpers


def _parse_library_book(wire: ReadarrLibraryBook) -> LibraryBook:
    book_file_count = (
        wire.statistics.book_file_count or 0
        if wire.statistics is not None and wire.statistics.book_file_count is not None
        else 0
    )
    author_id = wire.author_id or (wire.author.id if wire.author else None) or 0
    author_name = (wire.author.author_name if wire.author else None) or ""
    return LibraryBook(
        book_id=wire.id,
        author_id=author_id,
        author_name=author_name,
        title=wire.title or "",
        monitored=bool(wire.monitored),
        has_file=book_file_count > 0,
        release_date=wire.release_date,
    )


def _parse_book(wire: ReadarrWantedBook) -> MissingBook:
    author_id = wire.author_id or (wire.author.id if wire.author else None) or 0
    author_name = (wire.author.author_name if wire.author else None) or ""
    return MissingBook(
        book_id=wire.id,
        author_id=author_id,
        author_name=author_name,
        title=wire.title or "",
        release_date=wire.release_date,
    )

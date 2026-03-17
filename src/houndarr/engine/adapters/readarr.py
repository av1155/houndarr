"""Readarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.readarr.MissingBook` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.readarr.ReadarrClient`.
"""

from __future__ import annotations

from houndarr.clients.readarr import MissingBook, ReadarrClient
from houndarr.engine.candidates import SearchCandidate, _is_within_unreleased_delay
from houndarr.services.instances import Instance, ReadarrSearchMode

# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


def _book_label(item: MissingBook) -> str:
    """Build a human-readable log label for Readarr books."""
    author = item.author_name or "Unknown Author"
    title = item.title or "Unknown Book"
    return f"{author} - {title}"


def _author_context_label(item: MissingBook) -> str:
    """Build a log label for Readarr author-context search mode."""
    author = item.author_name or "Unknown Author"
    return f"{author} (author-context)"


def _author_item_id(author_id: int) -> int:
    """Return a stable, negative synthetic ID representing an author.

    Author-context searches are keyed on the author level, analogous to
    Lidarr's artist-context pattern.
    """
    return -(author_id * 1000)


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingBook, instance: Instance) -> SearchCandidate:
    """Convert a Readarr missing book into a :class:`SearchCandidate`.

    Args:
        item: A missing book returned by :meth:`ReadarrClient.get_missing`.
        instance: The configured Readarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    book_mode = instance.readarr_search_mode == ReadarrSearchMode.book

    use_author_context = not book_mode and item.author_id > 0

    if use_author_context:
        item_id = _author_item_id(item.author_id)
        label = _author_context_label(item)
        group_key: tuple[int, int] | None = (item.author_id, 0)
        search_payload = {
            "command": "AuthorSearch",
            "author_id": item.author_id,
        }
    else:
        item_id = item.book_id
        label = _book_label(item)
        group_key = None
        search_payload = {
            "command": "BookSearch",
            "book_id": item.book_id,
        }

    unreleased_reason: str | None = (
        f"unreleased delay ({instance.unreleased_delay_hrs}h)"
        if _is_within_unreleased_delay(item.release_date, instance.unreleased_delay_hrs)
        else None
    )

    return SearchCandidate(
        item_id=item_id,
        item_type="book",
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=group_key,
        search_payload=search_payload,
    )


def adapt_cutoff(item: MissingBook, instance: Instance) -> SearchCandidate:
    """Convert a Readarr cutoff-unmet book into a :class:`SearchCandidate`.

    Cutoff always uses book-mode regardless of ``readarr_search_mode``.

    Args:
        item: A cutoff-unmet book from :meth:`ReadarrClient.get_cutoff_unmet`.
        instance: The configured Readarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason: str | None = (
        f"unreleased delay ({instance.unreleased_delay_hrs}h)"
        if _is_within_unreleased_delay(item.release_date, instance.unreleased_delay_hrs)
        else None
    )

    return SearchCandidate(
        item_id=item.book_id,
        item_type="book",
        label=_book_label(item),
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload={
            "command": "BookSearch",
            "book_id": item.book_id,
        },
    )


async def dispatch_search(client: ReadarrClient, candidate: SearchCandidate) -> None:
    """Dispatch the appropriate Readarr search command for *candidate*.

    Args:
        client: An open :class:`ReadarrClient` context.
        candidate: The candidate to search for.

    Raises:
        ValueError: If ``search_payload["command"]`` is unrecognised.
    """
    command = candidate.search_payload["command"]
    if command == "AuthorSearch":
        await client.search_author(candidate.search_payload["author_id"])
    elif command == "BookSearch":
        await client.search(candidate.search_payload["book_id"])
    else:
        msg = f"Unknown Readarr search command: {command}"
        raise ValueError(msg)


def make_client(instance: Instance) -> ReadarrClient:
    """Construct a :class:`ReadarrClient` for *instance*.

    Args:
        instance: The configured Readarr instance.

    Returns:
        A new (unopened) :class:`ReadarrClient`.
    """
    return ReadarrClient(url=instance.url, api_key=instance.api_key)

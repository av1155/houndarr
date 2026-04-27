"""Readarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.readarr.MissingBook` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.readarr.ReadarrClient`.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from houndarr.clients.base import InstanceSnapshot, ReconcileSets
from houndarr.clients.readarr import LibraryBook, MissingBook, ReadarrClient
from houndarr.engine.adapters._common import (
    ContextOverride,
    build_cutoff_candidate,
    build_missing_candidate,
    compute_default_snapshot,
    paginate_wanted,
)
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
)
from houndarr.services.instances import Instance, ReadarrSearchMode

logger = logging.getLogger(__name__)

_UPGRADE_CUTOFF_EXCLUSION_HARD_CAP = 100

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


def _readarr_unreleased_reason(release_date: str | None, grace_hrs: int) -> str | None:
    """Return skip reason when a book should be treated as not yet searchable."""
    if _is_unreleased(release_date):
        return "not yet released"
    if _is_within_post_release_grace(release_date, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"
    return None


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
    unreleased_reason = _readarr_unreleased_reason(
        item.release_date, instance.missing.post_release_grace_hrs
    )

    context: ContextOverride | None = None
    if instance.missing.readarr_search_mode != ReadarrSearchMode.book and item.author_id > 0:
        context = ContextOverride(
            item_id=_author_item_id(item.author_id),
            label=_author_context_label(item),
            group_key=(item.author_id, 0),
            search_payload={
                "command": "AuthorSearch",
                "author_id": item.author_id,
            },
        )

    return build_missing_candidate(
        item_type="book",
        item_id=item.book_id,
        label=_book_label(item),
        unreleased_reason=unreleased_reason,
        search_payload={
            "command": "BookSearch",
            "book_id": item.book_id,
        },
        context=context,
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
    return build_cutoff_candidate(
        item_type="book",
        item_id=item.book_id,
        label=_book_label(item),
        unreleased_reason=_readarr_unreleased_reason(
            item.release_date, instance.missing.post_release_grace_hrs
        ),
        search_payload={
            "command": "BookSearch",
            "book_id": item.book_id,
        },
    )


def _library_book_label(item: LibraryBook) -> str:
    """Build a human-readable log label for library books."""
    author = item.author_name or "Unknown Author"
    title = item.title or "Unknown Book"
    return f"{author} - {title}"


def _library_author_context_label(item: LibraryBook) -> str:
    """Build a log label for library book in author-context mode."""
    author = item.author_name or "Unknown Author"
    return f"{author} (author-context)"


def adapt_upgrade(item: LibraryBook, instance: Instance) -> SearchCandidate:
    """Convert a Readarr library book into a :class:`SearchCandidate` for upgrade.

    Respects ``instance.upgrade.upgrade_readarr_search_mode`` for book vs author-context.
    No unreleased checks: upgrade items already have files.

    Args:
        item: A library book from :meth:`ReadarrClient.get_books`.
        instance: The configured Readarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    book_mode = instance.upgrade.upgrade_readarr_search_mode == ReadarrSearchMode.book

    use_author_context = not book_mode and item.author_id > 0

    if use_author_context:
        item_id = _author_item_id(item.author_id)
        label = _library_author_context_label(item)
        group_key: tuple[int, int] | None = (item.author_id, 0)
        search_payload = {
            "command": "AuthorSearch",
            "author_id": item.author_id,
        }
    else:
        item_id = item.book_id
        label = _library_book_label(item)
        group_key = None
        search_payload = {
            "command": "BookSearch",
            "book_id": item.book_id,
        }

    return SearchCandidate(
        item_id=item_id,
        item_type="book",
        label=label,
        unreleased_reason=None,
        group_key=group_key,
        search_payload=search_payload,
    )


async def fetch_upgrade_pool(
    client: ReadarrClient,
    instance: Instance,
) -> list[LibraryBook]:
    """Fetch and filter Readarr library for upgrade-eligible books.

    Builds a cutoff-unmet exclusion set by paginating ``wanted/cutoff``, then
    returns monitored books with files that are NOT in the exclusion set.

    The cutoff pagination stops naturally when a short page is returned
    (fewer records than the requested page_size).  A safety bound of
    ``_UPGRADE_CUTOFF_EXCLUSION_HARD_CAP`` pages prevents an unbounded
    walk if a misconfigured *arr instance returns full pages indefinitely;
    that bound is sized to cover libraries up to roughly
    ``hard_cap * 250`` cutoff-unmet books (default 25000), which is well
    above any real Readarr deployment we have observed.

    Args:
        client: An open :class:`ReadarrClient` context.
        instance: The configured Readarr instance.

    Returns:
        List of upgrade-eligible :class:`LibraryBook` items.
    """
    exclusion: set[int] = set()
    page = 1
    while page <= _UPGRADE_CUTOFF_EXCLUSION_HARD_CAP:
        try:
            cutoff_items = await client.get_cutoff_unmet(page=page, page_size=250)
        except (httpx.HTTPError, httpx.InvalidURL, ValidationError):
            logger.warning(
                "[%s] failed to fetch cutoff page %d for exclusion set",
                instance.core.name,
                page,
            )
            break
        for item in cutoff_items:
            exclusion.add(item.book_id)
        if len(cutoff_items) < 250:
            break
        page += 1
    if page > _UPGRADE_CUTOFF_EXCLUSION_HARD_CAP:
        logger.warning(
            "[%s] cutoff exclusion walk hit safety cap of %d pages; "
            "library has more than %d cutoff-unmet books and the "
            "upgrade pool may include some that are still cutoff-unmet",
            instance.core.name,
            _UPGRADE_CUTOFF_EXCLUSION_HARD_CAP,
            _UPGRADE_CUTOFF_EXCLUSION_HARD_CAP * 250,
        )

    library = await client.get_books()
    return [b for b in library if b.monitored and b.has_file and b.book_id not in exclusion]


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
    return ReadarrClient(url=instance.core.url, api_key=instance.core.api_key)


def _book_leaf_pairs(items: list[MissingBook]) -> frozenset[tuple[str, int]]:
    """Return the ``(item_type, book_id)`` pairs for a wanted list."""
    return frozenset(("book", it.book_id) for it in items if it.book_id)


def _author_synth_pairs(items: list[MissingBook]) -> frozenset[tuple[str, int]]:
    """Return synthetic author-context pairs for author-context cooldowns."""
    authors = {it.author_id for it in items if it.author_id and it.author_id > 0}
    return frozenset(("book", _author_item_id(aid)) for aid in authors)


async def fetch_reconcile_sets(client: ReadarrClient, instance: Instance) -> ReconcileSets:
    """Return the authoritative wanted / upgrade-pool sets for Readarr.

    Parallels the Lidarr implementation: leaf book ids always, plus
    synthetic negative author ids when the instance runs author-context
    missing-pass mode.  Cutoff cooldowns are always leaf.  When
    ``upgrade_enabled`` is false the upgrade set short-circuits to
    empty so the library scan + cutoff-exclusion paginate loop are
    skipped.
    """
    missing_items = await paginate_wanted(client.get_missing)
    cutoff_items = await paginate_wanted(client.get_cutoff_unmet)
    missing_set = _book_leaf_pairs(missing_items)
    cutoff_set = _book_leaf_pairs(cutoff_items)
    if instance.readarr_search_mode != ReadarrSearchMode.book:
        missing_set = missing_set | _author_synth_pairs(missing_items)
    upgrade_set: frozenset[tuple[str, int]] = frozenset()
    if instance.upgrade_enabled:
        upgrade_candidates = [
            adapt_upgrade(item, instance) for item in await fetch_upgrade_pool(client, instance)
        ]
        upgrade_set = frozenset((str(c.item_type), c.item_id) for c in upgrade_candidates)
    return ReconcileSets(missing=missing_set, cutoff=cutoff_set, upgrade=upgrade_set)


async def fetch_instance_snapshot(
    client: ReadarrClient,
    instance: Instance,  # noqa: ARG001
) -> InstanceSnapshot:
    """Compose the dashboard snapshot for a Readarr instance.

    Anchor for unreleased detection is :attr:`MissingBook.release_date`
    (single ISO string).  Books with no release date fall through to
    "already released", consistent with :func:`_readarr_unreleased_reason`.
    """
    return await compute_default_snapshot(
        client,
        anchor_fn=lambda bk: bk.release_date,
    )


class ReadarrAdapter:
    """Class-form Readarr adapter for the :data:`ADAPTERS` registry.

    Conforms to :class:`~houndarr.engine.adapters.protocols.AppAdapterProto`
    structurally via the eight staticmethod attributes below; the
    module-level functions remain importable for direct unit-test use.
    Track C.10 introduces this class form to replace the prior
    ``AppAdapter`` dataclass-of-callables registry shape.
    """

    adapt_missing = staticmethod(adapt_missing)
    adapt_cutoff = staticmethod(adapt_cutoff)
    adapt_upgrade = staticmethod(adapt_upgrade)
    fetch_upgrade_pool = staticmethod(fetch_upgrade_pool)
    dispatch_search = staticmethod(dispatch_search)
    make_client = staticmethod(make_client)
    fetch_reconcile_sets = staticmethod(fetch_reconcile_sets)
    fetch_instance_snapshot = staticmethod(fetch_instance_snapshot)

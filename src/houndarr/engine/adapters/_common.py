"""Shared adapter templates for the search engine pipeline.

Adapters today copy 85-100% of the same upgrade-pool builder, missing
candidate builder, and cutoff candidate builder per app.  This module
collects the shared templates so each adapter shrinks to per-app data
shaping plus a single call into here.

Track C.7 - C.9 land the templates and migrate the matching adapters;
C.10 then converts :class:`~houndarr.engine.adapters.AppAdapter` from a
dataclass of callables into a Protocol so adapters can become classes
that inherit the shared behaviour from a base instead of importing it
piecemeal.

Inhabitants:

- :func:`fetch_movie_upgrade_pool`: shared library-filter for the two
  movie adapters (Radarr, Whisparr v3).
- :class:`ContextOverride` + :func:`build_missing_candidate`: shared
  missing-pass candidate constructor.  Two-mode adapters
  (Sonarr / Whisparr v2 season-context, Lidarr artist-context, Readarr
  author-context) pass a ``ContextOverride`` when their per-instance
  search mode selects the parent-aggregate variant; single-mode
  adapters (Radarr, Whisparr v3) leave it as ``None``.
- :func:`build_cutoff_candidate`: shared cutoff-pass constructor.
  Single-mode for every app (cutoff always uses item-level dispatch
  even when missing-pass uses parent-context).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from houndarr.engine.candidates import ItemType, SearchCandidate


class _UpgradeFilterable(Protocol):
    """Library items the movie upgrade-pool filter understands.

    Both Radarr's :class:`~houndarr.clients.radarr.LibraryMovie` and
    Whisparr v3's :class:`~houndarr.clients.whisparr_v3.LibraryWhisparrV3Movie`
    structurally conform; episode, album, and book library items do not
    (their parent monitoring lives one level up so they take a different
    upgrade path entirely).

    The attributes are declared as ``@property`` so frozen dataclasses
    structurally satisfy the bound (the same pattern the
    :class:`~houndarr.engine.adapters.protocols.AppAdapterProto` uses
    for the same reason).
    """

    @property
    def monitored(self) -> bool: ...
    @property
    def has_file(self) -> bool: ...
    @property
    def cutoff_met(self) -> bool: ...


async def fetch_movie_upgrade_pool[T: _UpgradeFilterable](
    library_fetcher: Callable[[], Awaitable[list[T]]],
) -> list[T]:
    """Return upgrade-eligible items from a movie-shaped library.

    Calls *library_fetcher* once and filters the result to items that
    are monitored, already have a file, and have already met the
    quality cutoff.  Identical to the inline filter every per-adapter
    ``fetch_upgrade_pool`` used to carry; centralising it lets future
    changes to the upgrade-eligibility rule land in one place.

    Args:
        library_fetcher: A zero-arg awaitable returning the full
            library (typically ``client.get_library``).  Bound at the
            call site so the helper does not need to know how the
            client constructs the request.

    Returns:
        The filtered subset of the library, preserving fetch order.
    """
    library = await library_fetcher()
    return [m for m in library if m.monitored and m.has_file and m.cutoff_met]


_RECONCILE_PAGE_SIZE = 250
"""Page size used when paginating /wanted endpoints for reconciliation.

250 is the maximum every /wanted endpoint accepts.  Picking the ceiling
means a 5,000-item wanted list becomes 20 requests at refresh time — a
one-off cost per instance per snapshot cycle rather than a per-poll
overhead."""

_RECONCILE_MAX_PAGES = 200
"""Safety cap on the paginate-wanted loop.

At ``_RECONCILE_PAGE_SIZE=250`` items per page, 200 pages admit a
50,000-item wanted list, well beyond any realistic *arr library.  The
cap exists only to bound a misbehaving upstream that always returns
exactly ``page_size`` items (whether due to an off-by-one bug or a
hostile endpoint); without it the loop would never terminate."""


async def paginate_wanted[T](
    fetch_page: Callable[..., Awaitable[list[T]]],
    *,
    page_size: int = _RECONCILE_PAGE_SIZE,
) -> list[T]:
    """Return every wanted item by paginating *fetch_page* until exhausted.

    Stops on the first short page (length < *page_size*) since that is
    the last page by contract.  An explicit empty first page yields an
    empty list without a second request.  A hard cap of
    :data:`_RECONCILE_MAX_PAGES` bounds the loop so a misbehaving *arr
    that always returns a full page cannot spin forever; hitting the
    cap returns what has been collected so far and leaves reconcile to
    act conservatively against a possibly-truncated view.

    Used by every adapter's :func:`fetch_reconcile_sets` to walk the
    full wanted list so the reconciler sees a complete picture of which
    items are still wanted at this instant.

    Args:
        fetch_page: ``async def(page=N, page_size=M)`` callable.
            Typically bound to ``client.get_missing`` or
            ``client.get_cutoff_unmet``.
        page_size: Items per request.  Defaults to the /wanted endpoint
            maximum so total requests stay minimal at full library scale.

    Returns:
        Flat list of every item across every page, preserving order.
    """
    items: list[T] = []
    for page in range(1, _RECONCILE_MAX_PAGES + 1):
        chunk = await fetch_page(page=page, page_size=page_size)
        items.extend(chunk)
        if len(chunk) < page_size:
            return items
    return items


@dataclass(frozen=True, slots=True)
class ContextOverride:
    """Parent-context dispatch override for the missing-pass candidate.

    Four adapters (Sonarr, Whisparr v2, Lidarr, Readarr) optionally
    promote a per-item search to a per-parent search when their
    instance search-mode setting selects the parent-aggregate variant
    (``season_context`` for Sonarr / Whisparr v2; ``artist_context``
    for Lidarr; ``author_context`` for Readarr).  In context mode the
    candidate uses a synthetic negative item ID, a parent-scoped log
    label, a non-``None`` ``group_key`` for dispatch deduplication,
    and a different ``search_payload`` shape that the per-adapter
    ``dispatch_search`` reads.

    Single-mode adapters (Radarr, Whisparr v3) never set this; they
    always run per-item.  Cutoff-pass dispatch is always per-item even
    when the missing pass uses parent-context, so this override does
    not apply to :func:`build_cutoff_candidate`.
    """

    item_id: int
    label: str
    group_key: tuple[int, int]
    search_payload: dict[str, Any]


def build_missing_candidate(
    *,
    item_type: ItemType | str,
    item_id: int,
    label: str,
    unreleased_reason: str | None,
    search_payload: dict[str, Any],
    context: ContextOverride | None = None,
) -> SearchCandidate:
    """Construct a :class:`SearchCandidate` for the missing pass.

    When *context* is supplied the candidate uses the override fields
    (synthetic item_id, parent-scoped label, parent-context group_key,
    parent-shaped search_payload); when *context* is ``None`` the
    candidate uses the primary per-item fields.

    The unreleased-reason and item_type are shared regardless of
    dispatch mode; both pass through to the constructed candidate.

    Args:
        item_type: Per-adapter type string (``"movie"``, ``"episode"``,
            ``"album"``, ``"book"``, ``"whisparr_episode"``,
            ``"whisparr_v3_movie"``).
        item_id: The DB-stable per-item id used in primary mode.
            Ignored when *context* is supplied.
        label: Human-readable per-item log label.  Ignored when
            *context* is supplied.
        unreleased_reason: ``None`` when eligible; a skip-reason
            string when the candidate should be treated as unreleased.
        search_payload: Per-item dispatch payload.  Ignored when
            *context* is supplied.
        context: Optional parent-context override.  When set, replaces
            ``item_id`` / ``label`` / ``group_key`` / ``search_payload``
            and the resulting candidate dispatches at the parent level.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    if context is not None:
        return SearchCandidate(
            item_id=context.item_id,
            item_type=item_type,
            label=context.label,
            unreleased_reason=unreleased_reason,
            group_key=context.group_key,
            search_payload=context.search_payload,
        )
    return SearchCandidate(
        item_id=item_id,
        item_type=item_type,
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload=search_payload,
    )


def build_cutoff_candidate(
    *,
    item_type: ItemType | str,
    item_id: int,
    label: str,
    unreleased_reason: str | None,
    search_payload: dict[str, Any],
) -> SearchCandidate:
    """Construct a :class:`SearchCandidate` for the cutoff pass.

    The cutoff pass is single-mode for every *arr regardless of how the
    missing pass dispatches, so this helper takes no
    :class:`ContextOverride`.  Sonarr / Whisparr v2 / Lidarr / Readarr
    can run their missing pass under parent-context dispatch
    (``season_context``, ``artist_context``, ``author_context``) but
    their cutoff pass always dispatches per-item; Radarr and Whisparr
    v3 are single-mode end to end and delegate ``adapt_cutoff`` to
    ``adapt_missing`` directly.

    Args:
        item_type: Per-adapter type string (``"movie"``,
            ``"episode"``, ``"album"``, ``"book"``,
            ``"whisparr_episode"``, ``"whisparr_v3_movie"``).
        item_id: The DB-stable per-item id.
        label: Human-readable per-item log label.
        unreleased_reason: ``None`` when eligible; a skip-reason
            string when the candidate should be treated as unreleased.
        search_payload: Per-item dispatch payload.

    Returns:
        A fully populated :class:`SearchCandidate` with ``group_key``
        always ``None``.
    """
    return SearchCandidate(
        item_id=item_id,
        item_type=item_type,
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload=search_payload,
    )

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

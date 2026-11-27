"""Shared adapter templates for the search engine pipeline.

The upgrade-pool builder, missing-candidate builder, and
cutoff-candidate builder are identical across most *arr apps.  This
module collects the shared templates so each adapter shrinks to
per-app data shaping plus a single call into here.

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
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from typing import Any, Protocol

import httpx

from houndarr.clients.base import (
    ArrClient,
    InstanceSnapshot,
    WantedKind,
    WantedPage,
    WantedWalk,
    WantedWalks,
)
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_unreleased_dt,
)
from houndarr.errors import ClientError


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

The apps take any page size; 250 keeps a 5,000-item wanted list to 20
requests per refresh while each response stays small."""

_RECONCILE_RETRY_PAGE_SIZES = (239, 261)
"""Page sizes for re-reading a list whose tied rows came back reordered.

Each *arr sorts ``/wanted`` by one date column with no tie-breaker, and
PostgreSQL may order tied rows differently for each LIMIT/OFFSET, so a
tie group across a page boundary can repeat one row and skip another.
These sizes put their boundaries 11 rows per page away from 250's on
either side, so a re-read usually holds that group inside one page."""

_RECONCILE_MAX_PAGES = 200
"""Safety cap on the paginate-wanted loop.

At ``_RECONCILE_PAGE_SIZE=250`` items per page, 200 pages admit a
50,000-item wanted list, well beyond any realistic *arr library.  The
cap exists only to bound a misbehaving upstream that always returns
exactly ``page_size`` items (whether due to an off-by-one bug or a
hostile endpoint); without it the loop would never terminate."""


type WantedPageFetch[T] = Callable[..., Awaitable[WantedPage[T]]]
"""``async def(*, page, page_size)``: ``ArrClient.get_wanted_page`` bound to one kind."""


@dataclass(frozen=True, slots=True)
class _Pass[T]:
    """Everything one walk of a list read, at one page size."""

    items: list[T]
    ids: list[int]
    totals: list[int | None]
    counts_rows: bool
    capped: bool

    def total(self) -> int | None:
        """Return the one total every read reported, or ``None`` if they differ or lack one."""
        first = self.totals[0]
        if first is None or any(t != first for t in self.totals):
            return None
        return first

    def counted(self) -> int:
        return len(self.ids) if self.counts_rows else len(set(self.ids))

    def reordered(self) -> bool:
        """Return True when a steady list came back with a row repeated and another skipped.

        With the total unchanged, a full read of it repeating a row must
        also have skipped one.  A list counted by rows can repeat
        legitimately, so it cannot tell this from its own repeats.
        """
        total = self.total()
        return (
            not self.capped
            and not self.counts_rows
            and total is not None
            and len(self.ids) == total
            and len(set(self.ids)) < total
        )


async def _read_pass[T](fetch: WantedPageFetch[T], page_size: int) -> _Pass[T]:
    # The apps count after reading the page, so only a count taken before
    # page 1 catches an item that leaves between page 1's two queries.
    first = await fetch(page=1, page_size=1)
    totals = [first.total_records]
    items: list[T] = []
    ids: list[int] = []
    for page in range(1, _RECONCILE_MAX_PAGES + 1):
        chunk = await fetch(page=page, page_size=page_size)
        items.extend(chunk.items)
        ids.extend(chunk.ids)
        totals.append(chunk.total_records)
        if len(chunk.ids) < page_size:
            return _Pass(items, ids, totals, first.total_counts_rows, capped=False)
    return _Pass(items, ids, totals, first.total_counts_rows, capped=True)


def _verdict[T](walked: _Pass[T]) -> WantedWalk[T]:
    if walked.capped:
        return WantedWalk(walked.items, complete=False, capped=True)
    if None in walked.totals:
        # A proxy that strips totalRecords leaves the short page as the only signal.
        return WantedWalk(walked.items, complete=True)
    total = walked.total()
    if total is None or walked.counted() != total:
        return WantedWalk(walked.items, complete=False, churned=True)
    return WantedWalk(walked.items, complete=True)


async def read_wanted[T](fetch_page: WantedPageFetch[T]) -> WantedWalk[T]:
    """Read every page of one wanted list and say whether the reads account for all of it.

    Reads a size-1 page for the list's total first, then every page of
    :data:`_RECONCILE_PAGE_SIZE` up to the first short one, the last by
    contract.  Items keep their order and any repeats.

    The walk is incomplete when it stops at :data:`_RECONCILE_MAX_PAGES`
    with pages still full.  It is incomplete and churned when the totals
    differ between reads, or when the records read (distinct ids, or rows
    on a list that counts rows) do not match the total: the list changed
    or reordered between page reads, so an item may have gone unread.
    When any read lacks a total, the short page alone decides.

    When a steady list came back with a row repeated and another skipped
    (:meth:`_Pass.reordered`), each size in
    :data:`_RECONCILE_RETRY_PAGE_SIZES` reads it again until the reads
    together cover every item, skipping a size whose read would stop at
    the page cap.  Every read must report the same total, so a list that
    changed meanwhile stays churned, as does one whose re-read fails.  A
    covered list is complete and lists each item once, in the order first
    read.  A list the re-reads do not cover lists the first read's rows as
    returned, then each item a re-read that did not fail added.
    """
    first = await _read_pass(fetch_page, _RECONCILE_PAGE_SIZE)
    if not first.reordered():
        return _verdict(first)
    total = first.total()
    seen: set[int] = set()
    items = _first_of_each(first, seen)
    recovered: list[T] = []
    for size in _RECONCILE_RETRY_PAGE_SIZES:
        if len(first.ids) >= size * _RECONCILE_MAX_PAGES:
            continue
        try:
            retry = await _read_pass(fetch_page, size)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, ClientError):
            break
        recovered.extend(_first_of_each(retry, seen))
        if retry.capped or retry.total() != total:
            break
        if len(seen) == total:
            return WantedWalk([*items, *recovered], complete=True)
    # The upgrade pool excludes every item a walk returns, recovered ones included.
    return replace(_verdict(first), items=[*first.items, *recovered])


def _first_of_each[T](walked: _Pass[T], seen: set[int]) -> list[T]:
    """Return *walked*'s items whose id is not yet in *seen*, adding each id to it."""
    out: list[T] = []
    for item, item_id in zip(walked.items, walked.ids, strict=True):
        if item_id not in seen:
            seen.add(item_id)
            out.append(item)
    return out


async def walk_wanted[T](
    walks: WantedWalks | None,
    kind: WantedKind,
    fetch_page: Callable[..., Awaitable[WantedPage[T]]],
) -> WantedWalk[T]:
    """Return the :func:`read_wanted` walk of ``/wanted/{kind}``, reusing one already in *walks*.

    *fetch_page* is ``client.get_wanted_page``.  A walk that raises is not
    stored, so the next caller tries again.
    """
    if walks is not None and kind in walks:
        return walks[kind]
    walk = await read_wanted(partial(fetch_page, kind))
    if walks is not None:
        walks[kind] = walk
    return walk


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
    item_type: str,
    item_id: int,
    label: str,
    unreleased_reason: str | None,
    search_payload: dict[str, Any],
    context: ContextOverride | None = None,
    tags: tuple[int, ...] = (),
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
            ``"album"``, ``"book"``, ``"whisparr_v2_episode"``,
            ``"whisparr_v3_movie"``).
        item_id: The DB-stable per-item id used in primary mode.  When
            *context* is supplied it becomes the candidate's ``leaf_id``
            instead, so the engine can still match the record against
            the download queue.
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
            tags=tags,
            leaf_id=item_id,
        )
    return SearchCandidate(
        item_id=item_id,
        item_type=item_type,
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload=search_payload,
        tags=tags,
    )


def build_cutoff_candidate(
    *,
    item_type: str,
    item_id: int,
    label: str,
    unreleased_reason: str | None,
    search_payload: dict[str, Any],
    tags: tuple[int, ...] = (),
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
            ``"whisparr_v2_episode"``, ``"whisparr_v3_movie"``).
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
        tags=tags,
    )


async def compute_default_snapshot[T](
    client: ArrClient,
    *,
    anchor_fn: Callable[[T], str | None] | Callable[[T], datetime | None],
    anchor_is_dt: bool = False,
    walks: WantedWalks | None = None,
    cutoff_id_fn: Callable[[Any], int] | None = None,
) -> InstanceSnapshot:
    """Build an :class:`InstanceSnapshot` from a ``/wanted``-paged client.

    Used by the five paginated adapters (Sonarr, Radarr, Lidarr,
    Readarr, Whisparr v2).  Whisparr v3 has no ``/wanted`` endpoint and
    composes its own snapshot in one cached library walk.

    The composition has two pieces:

    - ``monitored_total``: the sum of ``totalRecords`` for missing +
      cutoff via :meth:`ArrClient.get_wanted_total`.  Each call is a
      cheap ``pageSize=1`` probe.  An app whose cutoff total counts
      rows (Readarr: one per below-cutoff file) passes *cutoff_id_fn*,
      and the cutoff part counts the distinct ids of the cutoff walk
      instead, falling back to the probe when that walk stops at the
      page cap.  A cutoff walk that raises fails the snapshot, as the
      missing walk does.
    - ``unreleased_count``: walks ``/wanted/missing`` once via
      :func:`walk_wanted` and counts items whose
      ``anchor_fn(item)`` resolves as still-in-the-future per
      :func:`_is_unreleased` (string anchor) or
      :func:`_is_unreleased_dt` (pre-parsed datetime anchor).  Cutoff
      items never count: by definition they have a file, so they are
      released.

    The caller supplies the per-app anchor selector.  For Sonarr it is
    ``lambda ep: ep.air_date_utc``; for Radarr it is the multi-fallback
    ``_radarr_release_anchor``; etc.  This keeps the helper agnostic
    of the wire model and lets each adapter own its canonical anchor
    exactly once.

    Args:
        client: An open :class:`ArrClient` (any /wanted-paged subclass).
        anchor_fn: Selector that returns the canonical "release anchor"
            for a missing-pass item.  Either an ISO string or a
            ``datetime`` (controlled by ``anchor_is_dt``).
        anchor_is_dt: When ``True`` the anchor is a pre-parsed
            ``datetime``; pick the dt-typed unreleased helper.  Default
            ``False`` keeps the string path.
        walks: This refresh's walk memo, shared with the reconcile fetch.
        cutoff_id_fn: Selector for a cutoff item's id, for an app whose
            cutoff total counts rows.

    Returns:
        :class:`InstanceSnapshot` with both counts populated.
    """
    monitored_total = await client.get_wanted_total("missing")
    if cutoff_id_fn is None:
        monitored_total += await client.get_wanted_total("cutoff")
    else:
        monitored_total += await _distinct_cutoff_total(client, walks, cutoff_id_fn)

    walk: WantedWalk[T] = await walk_wanted(walks, "missing", client.get_wanted_page)
    items = walk.items
    if anchor_is_dt:
        dt_fn: Callable[[T], datetime | None] = anchor_fn  # type: ignore[assignment]
        unreleased_count = sum(1 for item in items if _is_unreleased_dt(dt_fn(item)))
    else:
        str_fn: Callable[[T], str | None] = anchor_fn  # type: ignore[assignment]
        unreleased_count = sum(1 for item in items if _is_unreleased(str_fn(item)))

    return InstanceSnapshot(
        monitored_total=monitored_total,
        unreleased_count=unreleased_count,
    )


async def _distinct_cutoff_total(
    client: ArrClient,
    walks: WantedWalks | None,
    id_fn: Callable[[Any], int],
) -> int:
    """Return how many distinct items the cutoff list holds, or its row total past the page cap."""
    walk = await walk_wanted(walks, "cutoff", client.get_wanted_page)
    if walk.capped:
        return await client.get_wanted_total("cutoff")
    return len({id_fn(item) for item in walk.items})

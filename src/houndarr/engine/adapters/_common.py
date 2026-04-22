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

The first inhabitant is :func:`fetch_movie_upgrade_pool`, used by the
two movie-shaped clients (Radarr and Whisparr v3) whose library item
exposes the three boolean flags every upgrade pass filters on.  Series,
album, and book libraries iterate the parent aggregate first and so use
their own per-adapter pool builders that this helper cannot subsume.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol


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

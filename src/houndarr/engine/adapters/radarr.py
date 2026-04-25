"""Radarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.radarr.MissingMovie` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.radarr.RadarrClient`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from houndarr.clients.base import ReconcileSets
from houndarr.clients.radarr import LibraryMovie, MissingMovie, RadarrClient
from houndarr.engine.adapters._common import (
    build_missing_candidate,
    fetch_movie_upgrade_pool,
    paginate_wanted,
)
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
)
from houndarr.services.instances import Instance

_RADARR_UNRELEASED_STATUSES = {"tba", "announced"}


# ---------------------------------------------------------------------------
# Helpers (copied from search_loop.py; originals removed in Phase 2)
# ---------------------------------------------------------------------------


def _radarr_release_anchor(movie: MissingMovie) -> str | None:
    """Return preferred Radarr release anchor in fallback order."""
    return movie.digital_release or movie.physical_release or movie.release_date or movie.in_cinemas


def _radarr_unreleased_reason(movie: MissingMovie, grace_hrs: int) -> str | None:
    """Return skip reason when a Radarr movie should be treated as not yet searchable."""
    release_anchor = _radarr_release_anchor(movie)
    if _is_unreleased(release_anchor):
        return "not yet released"
    if _is_within_post_release_grace(release_anchor, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"

    if movie.is_available is False:
        return "radarr reports not available"

    status = (movie.status or "").lower()
    if status in _RADARR_UNRELEASED_STATUSES and movie.is_available is not True:
        return "radarr status indicates unreleased"

    if (
        movie.year > datetime.now(UTC).year
        and movie.is_available is not True
        and status != "released"
    ):
        return "future title not yet available"

    return None


def _movie_label(item: MissingMovie) -> str:
    """Build a human-readable log label for Radarr movies."""
    title = item.title or "Unknown Movie"
    if item.year > 0:
        return f"{title} ({item.year})"
    return title


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingMovie, instance: Instance) -> SearchCandidate:
    """Convert a Radarr missing movie into a :class:`SearchCandidate`.

    Args:
        item: A missing movie returned by :meth:`RadarrClient.get_missing`.
        instance: The configured Radarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return build_missing_candidate(
        item_type="movie",
        item_id=item.movie_id,
        label=_movie_label(item),
        unreleased_reason=_radarr_unreleased_reason(item, instance.post_release_grace_hrs),
        search_payload={
            "command": "MoviesSearch",
            "movie_id": item.movie_id,
        },
    )


def adapt_cutoff(item: MissingMovie, instance: Instance) -> SearchCandidate:
    """Convert a Radarr cutoff-unmet movie into a :class:`SearchCandidate`.

    The logic is identical to :func:`adapt_missing`; Radarr applies the
    same unreleased checks and label format for both search kinds.

    Args:
        item: A cutoff-unmet movie from :meth:`RadarrClient.get_cutoff_unmet`.
        instance: The configured Radarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return adapt_missing(item, instance)


def _library_movie_label(item: LibraryMovie) -> str:
    """Build a human-readable log label for Radarr library movies."""
    title = item.title or "Unknown Movie"
    if item.year > 0:
        return f"{title} ({item.year})"
    return title


def adapt_upgrade(item: LibraryMovie, instance: Instance) -> SearchCandidate:
    """Convert a Radarr library movie into a :class:`SearchCandidate` for upgrade.

    No unreleased checks: upgrade items already have files.

    Args:
        item: A library movie from :meth:`RadarrClient.get_library`.
        instance: The configured Radarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return SearchCandidate(
        item_id=item.movie_id,
        item_type="movie",
        label=_library_movie_label(item),
        unreleased_reason=None,
        group_key=None,
        search_payload={
            "command": "MoviesSearch",
            "movie_id": item.movie_id,
        },
    )


async def fetch_upgrade_pool(
    client: RadarrClient,
    instance: Instance,  # noqa: ARG001
) -> list[LibraryMovie]:
    """Fetch and filter Radarr library for upgrade-eligible movies.

    Returns monitored movies that have a file and have met their quality cutoff.

    Args:
        client: An open :class:`RadarrClient` context.
        instance: The configured Radarr instance.  Unused at present;
            kept for AppAdapter signature parity with the series /
            album / book adapters whose pool builders consult instance
            policy.

    Returns:
        List of upgrade-eligible :class:`LibraryMovie` items.
    """
    return await fetch_movie_upgrade_pool(client.get_library)


async def dispatch_search(client: RadarrClient, candidate: SearchCandidate) -> None:
    """Dispatch a Radarr search command for *candidate*.

    Args:
        client: An open :class:`RadarrClient` context.
        candidate: The candidate to search for.
    """
    await client.search(candidate.search_payload["movie_id"])


async def fetch_reconcile_sets(
    client: RadarrClient,
    instance: Instance,
) -> ReconcileSets:
    """Return the authoritative wanted / upgrade-pool sets for Radarr.

    Radarr has no parent-context mode: cooldown rows always carry the
    leaf ``movie_id``, so the reconciliation sets are just the leaf
    ids from each pass.  When ``upgrade_enabled`` is false the upgrade
    set short-circuits to empty so the ``/movie`` library call is
    skipped.
    """
    missing_items = await paginate_wanted(client.get_missing)
    cutoff_items = await paginate_wanted(client.get_cutoff_unmet)
    upgrade_set: frozenset[tuple[str, int]] = frozenset()
    if instance.upgrade_enabled:
        upgrade_candidates = [
            adapt_upgrade(item, instance) for item in await fetch_upgrade_pool(client, instance)
        ]
        upgrade_set = frozenset((str(c.item_type), c.item_id) for c in upgrade_candidates)
    return ReconcileSets(
        missing=frozenset(("movie", m.movie_id) for m in missing_items),
        cutoff=frozenset(("movie", m.movie_id) for m in cutoff_items),
        upgrade=upgrade_set,
    )


def make_client(instance: Instance) -> RadarrClient:
    """Construct a :class:`RadarrClient` for *instance*.

    Args:
        instance: The configured Radarr instance.

    Returns:
        A new (unopened) :class:`RadarrClient`.
    """
    return RadarrClient(url=instance.url, api_key=instance.api_key)


class RadarrAdapter:
    """Class-form Radarr adapter for the :data:`ADAPTERS` registry.

    Conforms to :class:`~houndarr.engine.adapters.protocols.AppAdapterProto`
    structurally via the six staticmethod attributes below; the
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

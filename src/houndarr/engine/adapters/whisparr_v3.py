"""Whisparr v3 adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.whisparr_v3.MissingWhisparrV3Movie` instances
into :class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.whisparr_v3.WhisparrV3Client`.

Whisparr v3 is Radarr-based, so this adapter follows the same patterns as the
Radarr adapter: movie-level search, no group keys, and the same 4-layer
unreleased eligibility logic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from houndarr.clients.base import InstanceSnapshot, ReconcileSets
from houndarr.clients.whisparr_v3 import (
    LibraryWhisparrV3Movie,
    MissingWhisparrV3Movie,
    WhisparrV3Client,
)
from houndarr.engine.adapters._common import (
    build_missing_candidate,
    fetch_movie_upgrade_pool,
)
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
    _parse_iso_utc,
)
from houndarr.services.instances import Instance

_UNRELEASED_STATUSES = {"tba", "announced"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _whisparr_v3_release_anchor(movie: MissingWhisparrV3Movie) -> str | None:
    """Return preferred release anchor in fallback order."""
    return movie.digital_release or movie.physical_release or movie.release_date or movie.in_cinemas


def _whisparr_v3_unreleased_reason(movie: MissingWhisparrV3Movie, grace_hrs: int) -> str | None:
    """Return skip reason when a Whisparr v3 movie should be treated as not yet searchable."""
    release_anchor = _whisparr_v3_release_anchor(movie)
    if _is_unreleased(release_anchor):
        return "not yet released"
    if _is_within_post_release_grace(release_anchor, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"

    if movie.is_available is False:
        return "whisparr v3 reports not available"

    status = (movie.status or "").lower()
    if status in _UNRELEASED_STATUSES and movie.is_available is not True:
        return "whisparr v3 status indicates unreleased"

    if (
        movie.year > datetime.now(UTC).year
        and movie.is_available is not True
        and status != "released"
    ):
        return "future title not yet available"

    return None


def _movie_label(item: MissingWhisparrV3Movie) -> str:
    """Build a human-readable log label for Whisparr v3 movies/scenes."""
    title = item.title or "Unknown Movie"
    if item.year > 0:
        return f"{title} ({item.year})"
    return title


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingWhisparrV3Movie, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr v3 missing movie into a :class:`SearchCandidate`.

    Args:
        item: A missing movie from :meth:`WhisparrV3Client.get_missing`.
        instance: The configured Whisparr v3 instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return build_missing_candidate(
        item_type="whisparr_v3_movie",
        item_id=item.movie_id,
        label=_movie_label(item),
        unreleased_reason=_whisparr_v3_unreleased_reason(
            item, instance.missing.post_release_grace_hrs
        ),
        search_payload={
            "command": "MoviesSearch",
            "movie_id": item.movie_id,
        },
    )


def adapt_cutoff(item: MissingWhisparrV3Movie, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr v3 cutoff-unmet movie into a :class:`SearchCandidate`.

    Args:
        item: A cutoff-unmet movie from :meth:`WhisparrV3Client.get_cutoff_unmet`.
        instance: The configured Whisparr v3 instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return adapt_missing(item, instance)


def _library_movie_label(item: LibraryWhisparrV3Movie) -> str:
    """Build a human-readable log label for Whisparr v3 library movies/scenes."""
    title = item.title or "Unknown Movie"
    if item.year > 0:
        return f"{title} ({item.year})"
    return title


def adapt_upgrade(item: LibraryWhisparrV3Movie, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr v3 library movie into a :class:`SearchCandidate` for upgrade.

    No unreleased checks: upgrade items already have files.

    Args:
        item: A library movie from :meth:`WhisparrV3Client.get_library`.
        instance: The configured Whisparr v3 instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return SearchCandidate(
        item_id=item.movie_id,
        item_type="whisparr_v3_movie",
        label=_library_movie_label(item),
        unreleased_reason=None,
        group_key=None,
        search_payload={
            "command": "MoviesSearch",
            "movie_id": item.movie_id,
        },
    )


async def fetch_upgrade_pool(
    client: WhisparrV3Client,
    instance: Instance,  # noqa: ARG001
) -> list[LibraryWhisparrV3Movie]:
    """Fetch and filter the Whisparr v3 library for upgrade-eligible movies/scenes.

    Returns monitored items that have a file and have met their quality cutoff.

    Args:
        client: An open :class:`WhisparrV3Client` context.
        instance: The configured Whisparr v3 instance.  Unused at
            present; kept for AppAdapter signature parity with the
            series / album / book adapters whose pool builders consult
            instance policy.

    Returns:
        List of upgrade-eligible :class:`LibraryWhisparrV3Movie` items.
    """
    return await fetch_movie_upgrade_pool(client.get_library)


async def dispatch_search(client: WhisparrV3Client, candidate: SearchCandidate) -> None:
    """Dispatch a Whisparr v3 search command for *candidate*.

    Args:
        client: An open :class:`WhisparrV3Client` context.
        candidate: The candidate to search for.
    """
    await client.search(candidate.search_payload["movie_id"])


def make_client(instance: Instance) -> WhisparrV3Client:
    """Construct a :class:`WhisparrV3Client` for *instance*.

    Args:
        instance: The configured Whisparr v3 instance.

    Returns:
        A new (unopened) :class:`WhisparrV3Client`.
    """
    return WhisparrV3Client(url=instance.core.url, api_key=instance.core.api_key)


async def fetch_reconcile_sets(
    client: WhisparrV3Client,
    instance: Instance,  # noqa: ARG001
) -> ReconcileSets:
    """Return the authoritative wanted / upgrade-pool sets for Whisparr v3.

    Whisparr v3 has no ``/wanted`` endpoint; the client already caches
    the full ``/api/v3/movie`` response for the whole lifetime of one
    client context.  Read that cache three ways to build the sets
    without additional network calls: missing = monitored without
    file; cutoff = monitored with file but cutoff-unmet; upgrade =
    monitored with file and cutoff-met.  There is no context-mode
    variant to handle.
    """
    library = await client.get_library()
    missing_ids: set[int] = set()
    cutoff_ids: set[int] = set()
    upgrade_ids: set[int] = set()
    for movie in library:
        if not movie.monitored:
            continue
        if not movie.has_file:
            missing_ids.add(movie.movie_id)
        elif not movie.cutoff_met:
            cutoff_ids.add(movie.movie_id)
        else:
            upgrade_ids.add(movie.movie_id)
    return ReconcileSets(
        missing=frozenset(("whisparr_v3_movie", mid) for mid in missing_ids),
        cutoff=frozenset(("whisparr_v3_movie", mid) for mid in cutoff_ids),
        upgrade=frozenset(("whisparr_v3_movie", mid) for mid in upgrade_ids),
    )


async def fetch_instance_snapshot(
    client: WhisparrV3Client,
    instance: Instance,  # noqa: ARG001
) -> InstanceSnapshot:
    """Compose the dashboard snapshot for a Whisparr v3 instance.

    Whisparr v3 has no ``/wanted`` endpoint, so the snapshot does not
    follow the shared :func:`compute_default_snapshot` path.  Instead
    it walks the cached ``/api/v3/movie`` response (also consumed by
    :func:`fetch_reconcile_sets` in the same client context, so the
    cost is amortised to a single HTTP round trip per refresh cycle).

    ``monitored_total`` counts items that are monitored AND either
    have no file OR have a file but the cutoff is unmet — matching
    the missing + cutoff sum the other adapters derive from
    ``totalRecords``.  ``unreleased_count`` counts monitored items
    whose first parseable release anchor (digital → physical →
    in-cinemas → release_date) is strictly in the future.  This
    intentionally stays narrower than :func:`_whisparr_v3_unreleased_reason` at
    dispatch time, which adds ``isAvailable=false`` and status-based
    gates; the dashboard's Unreleased bucket reflects strictly
    pre-release items, while items skipped for those other reasons
    surface in the logs as their explicit reason strings.
    """
    movies = await client.get_library()
    now = datetime.now(UTC)
    monitored_total = 0
    unreleased_count = 0
    for m in movies:
        if not m.monitored:
            continue
        if (not m.has_file) or (m.has_file and not m.cutoff_met):
            monitored_total += 1
        for val in (m.digital_release, m.physical_release, m.in_cinemas, m.release_date):
            parsed = _parse_iso_utc(val)
            if parsed is not None and parsed > now:
                unreleased_count += 1
                break
    return InstanceSnapshot(
        monitored_total=monitored_total,
        unreleased_count=unreleased_count,
    )


class WhisparrV3Adapter:
    """Class-form Whisparr v3 adapter for the :data:`ADAPTERS` registry.

    Conforms to :class:`~houndarr.engine.adapters.protocols.AppAdapterProto`
    structurally via the eight staticmethod attributes below; the
    module-level functions remain importable for direct unit-test use.
    """

    adapt_missing = staticmethod(adapt_missing)
    adapt_cutoff = staticmethod(adapt_cutoff)
    adapt_upgrade = staticmethod(adapt_upgrade)
    fetch_upgrade_pool = staticmethod(fetch_upgrade_pool)
    dispatch_search = staticmethod(dispatch_search)
    make_client = staticmethod(make_client)
    fetch_reconcile_sets = staticmethod(fetch_reconcile_sets)
    fetch_instance_snapshot = staticmethod(fetch_instance_snapshot)

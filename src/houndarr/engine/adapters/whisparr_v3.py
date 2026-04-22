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
)
from houndarr.services.instances import Instance

_UNRELEASED_STATUSES = {"tba", "announced"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _release_anchor(movie: MissingWhisparrV3Movie) -> str | None:
    """Return preferred release anchor in fallback order."""
    return movie.digital_release or movie.physical_release or movie.release_date or movie.in_cinemas


def _unreleased_reason(movie: MissingWhisparrV3Movie, grace_hrs: int) -> str | None:
    """Return skip reason when a Whisparr v3 movie should be treated as not yet searchable."""
    release_anchor = _release_anchor(movie)
    if _is_unreleased(release_anchor):
        return "not yet released"
    if _is_within_post_release_grace(release_anchor, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"

    if movie.is_available is False:
        return "whisparr reports not available"

    status = (movie.status or "").lower()
    if status in _UNRELEASED_STATUSES and movie.is_available is not True:
        return "whisparr status indicates unreleased"

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
        unreleased_reason=_unreleased_reason(item, instance.post_release_grace_hrs),
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
    return WhisparrV3Client(url=instance.url, api_key=instance.api_key)


class WhisparrV3Adapter:
    """Class-form Whisparr v3 adapter for the :data:`ADAPTERS` registry.

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

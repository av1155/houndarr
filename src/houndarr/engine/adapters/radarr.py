"""Radarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.radarr.MissingMovie` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.radarr.RadarrClient`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from houndarr.clients.radarr import MissingMovie, RadarrClient
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
)
from houndarr.services.instances import Instance

_RADARR_UNRELEASED_STATUSES = {"tba", "announced"}


# ---------------------------------------------------------------------------
# Helpers (copied from search_loop.py — originals removed in Phase 2)
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
    return SearchCandidate(
        item_id=item.movie_id,
        item_type="movie",
        label=_movie_label(item),
        unreleased_reason=_radarr_unreleased_reason(item, instance.post_release_grace_hrs),
        group_key=None,
        search_payload={
            "command": "MoviesSearch",
            "movie_id": item.movie_id,
        },
    )


def adapt_cutoff(item: MissingMovie, instance: Instance) -> SearchCandidate:
    """Convert a Radarr cutoff-unmet movie into a :class:`SearchCandidate`.

    The logic is identical to :func:`adapt_missing` — Radarr applies the
    same unreleased checks and label format for both search kinds.

    Args:
        item: A cutoff-unmet movie from :meth:`RadarrClient.get_cutoff_unmet`.
        instance: The configured Radarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return adapt_missing(item, instance)


async def dispatch_search(client: RadarrClient, candidate: SearchCandidate) -> None:
    """Dispatch a Radarr search command for *candidate*.

    Args:
        client: An open :class:`RadarrClient` context.
        candidate: The candidate to search for.
    """
    await client.search(candidate.search_payload["movie_id"])


def make_client(instance: Instance) -> RadarrClient:
    """Construct a :class:`RadarrClient` for *instance*.

    Args:
        instance: The configured Radarr instance.

    Returns:
        A new (unopened) :class:`RadarrClient`.
    """
    return RadarrClient(url=instance.url, api_key=instance.api_key)

"""Normalized search candidate model for the engine pipeline.

:class:`SearchCandidate` is the unified representation that adapter functions
produce from app-specific client models (``MissingEpisode``, ``MissingMovie``,
``MissingAlbum``, ``MissingBook``, ``MissingWhisparrEpisode``).  The engine
pipeline operates solely on ``SearchCandidate`` instances, removing the need
for ``isinstance`` checks or per-app branching.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from houndarr.enums import ItemType

__all__ = ["ItemType", "SearchCandidate"]


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """A normalized item ready for the search pipeline.

    Adapter functions convert app-specific models into this common shape.
    The engine sees only ``SearchCandidate``; it never inspects the
    original app-specific model.

    Attributes:
        item_id: Episode ID, movie ID, album ID, book ID, or synthetic
            season/artist/author ID.
        item_type: One of ``"episode"``, ``"movie"``, ``"album"``,
            ``"book"``, or ``"whisparr_episode"``.
        label: Human-readable label for logging.
        unreleased_reason: ``None`` when eligible; a skip-reason string
            when the item should be treated as unreleased.
        group_key: ``(series_id, season)`` for season-context dedup,
            ``(artist_id,)`` for artist-context, ``(author_id,)`` for
            author-context, or ``None`` for item-level modes.
        search_payload: Opaque data consumed only by the adapter's
            ``dispatch_search`` function.
    """

    item_id: int
    item_type: ItemType | str
    label: str
    unreleased_reason: str | None
    group_key: tuple[int, int] | None
    search_payload: dict[str, Any]


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 value into a timezone-aware UTC datetime."""
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_unreleased(release_at: str | None) -> bool:
    """Return True when an item has not yet been released.

    Items with no release date are treated as released (eligible for search)
    because the *arr app has already classified them as wanted.
    """
    release_dt = _parse_iso_utc(release_at)
    if release_dt is None:
        return False
    return datetime.now(UTC) < release_dt


def _is_unreleased_dt(release_dt: datetime | None) -> bool:
    """Return True when a pre-parsed datetime is in the future.

    Sibling of :func:`_is_unreleased` for adapters whose wire layer
    already emits a ``datetime`` rather than an ISO string (today only
    Whisparr v2, whose ``releaseDate`` field can arrive as either a
    string or a ``{year, month, day}`` dict and is normalised at parse
    time).  Behaviour matches :func:`_is_unreleased`: ``None`` reads as
    "already released" so a missing date never inflates the unreleased
    bucket; naive datetimes are coerced to UTC defensively.
    """
    if release_dt is None:
        return False
    if release_dt.tzinfo is None:
        release_dt = release_dt.replace(tzinfo=UTC)
    return datetime.now(UTC) < release_dt


def _is_within_post_release_grace(release_at: str | None, grace_hrs: int) -> bool:
    """Return True when an item is released but still inside the grace period.

    The grace period gives indexers time to process newly released content.
    Returns False for truly unreleased items (use :func:`_is_unreleased` first).
    """
    if grace_hrs <= 0:
        return False

    release_dt = _parse_iso_utc(release_at)
    if release_dt is None:
        return False

    now = datetime.now(UTC)
    # Only applies to already-released items within the grace window.
    return release_dt <= now < (release_dt + timedelta(hours=grace_hrs))


def _is_within_unreleased_delay(release_at: str | None, unreleased_delay_hrs: int) -> bool:
    """Return True when an item is still inside the configured unreleased delay.

    .. deprecated::
        Kept for backward compatibility during the transition.  New adapter
        code should use :func:`_is_unreleased` and
        :func:`_is_within_post_release_grace` instead.
    """
    if unreleased_delay_hrs <= 0:
        return False

    release_dt = _parse_iso_utc(release_at)
    if release_dt is None:
        return False

    return datetime.now(UTC) < (release_dt + timedelta(hours=unreleased_delay_hrs))

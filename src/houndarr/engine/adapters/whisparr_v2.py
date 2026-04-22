"""Whisparr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.whisparr.MissingWhisparrEpisode` instances
into :class:`~houndarr.engine.candidates.SearchCandidate` and dispatches
search commands via :class:`~houndarr.clients.whisparr.WhisparrClient`.

Whisparr is a Sonarr fork, so the adapter structure mirrors the Sonarr adapter
with two key differences: the item type is ``whisparr_episode`` (not shared
with Sonarr), and episode labels omit ``episodeNumber`` (absent in Whisparr).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from houndarr.clients.whisparr_v2 import (
    LibraryWhisparrEpisode,
    MissingWhisparrEpisode,
    WhisparrClient,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, WhisparrSearchMode

logger = logging.getLogger(__name__)

_UPGRADE_MAX_SERIES_PER_CYCLE = 5

# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


def _episode_label(item: MissingWhisparrEpisode) -> str:
    """Build a human-readable log label for Whisparr episodes.

    Whisparr has no ``episodeNumber``; labels use season only.
    """
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - S{item.season_number:02d} - {item.episode_title}"
    return f"{series} - S{item.season_number:02d}"


def _season_context_label(item: MissingWhisparrEpisode) -> str:
    """Build a log label for Whisparr season-context search mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season_number:02d} (season-context)"


def _season_item_id(series_id: int, season_number: int) -> int:
    """Return a stable, negative synthetic ID representing a (series, season) pair.

    Uses the same scheme as the Sonarr adapter: ``-(series_id * 1000 + season_number)``.
    Collision with Sonarr is impossible because cooldowns and search_log are
    scoped by ``instance_id``.
    """
    return -(series_id * 1000 + season_number)


def _whisparr_unreleased_reason(
    release_dt: datetime | None,
    grace_hrs: int,
) -> str | None:
    """Return skip reason when a Whisparr episode is not yet searchable.

    Whisparr provides a parsed ``datetime`` (from DateOnly) rather than an
    ISO-8601 string, so this helper operates on ``datetime`` directly.
    """
    if release_dt is None:
        return None
    now = datetime.now(UTC)
    if now < release_dt:
        return "not yet released"
    if grace_hrs > 0 and now < (release_dt + timedelta(hours=grace_hrs)):
        return f"post-release grace ({grace_hrs}h)"
    return None


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingWhisparrEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr missing episode into a :class:`SearchCandidate`.

    Args:
        item: A missing episode returned by :meth:`WhisparrClient.get_missing`.
        instance: The configured Whisparr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    episode_mode = instance.whisparr_search_mode == WhisparrSearchMode.episode

    use_season_context = not episode_mode and item.series_id is not None and item.season_number > 0

    if use_season_context:
        assert item.series_id is not None  # noqa: S101
        item_id = _season_item_id(item.series_id, item.season_number)
        label = _season_context_label(item)
        group_key: tuple[int, int] | None = (item.series_id, item.season_number)
        search_payload = {
            "command": "SeasonSearch",
            "series_id": item.series_id,
            "season_number": item.season_number,
        }
    else:
        item_id = item.episode_id
        label = _episode_label(item)
        group_key = None
        search_payload = {
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        }

    unreleased_reason = _whisparr_unreleased_reason(
        item.release_date, instance.post_release_grace_hrs
    )

    # Episodes without any series linkage (series_id is None means both
    # seriesId and series.id were absent from the API response) are orphan
    # records that Whisparr cannot reliably search. Skip them so the pipeline
    # logs a clean "skipped" row instead of a dispatch-then-fail "error" row.
    # Season-0 specials with a valid series_id are unaffected.
    if item.series_id is None and unreleased_reason is None:
        unreleased_reason = "no series linked"

    return SearchCandidate(
        item_id=item_id,
        item_type="whisparr_episode",
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=group_key,
        search_payload=search_payload,
    )


def adapt_cutoff(item: MissingWhisparrEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr cutoff-unmet episode into a :class:`SearchCandidate`.

    Cutoff always uses episode-mode regardless of ``whisparr_search_mode``.

    Args:
        item: A cutoff-unmet episode from :meth:`WhisparrClient.get_cutoff_unmet`.
        instance: The configured Whisparr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason = _whisparr_unreleased_reason(
        item.release_date, instance.post_release_grace_hrs
    )

    # Same orphan guard as adapt_missing: skip records with no series linkage.
    if item.series_id is None and unreleased_reason is None:
        unreleased_reason = "no series linked"

    return SearchCandidate(
        item_id=item.episode_id,
        item_type="whisparr_episode",
        label=_episode_label(item),
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload={
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        },
    )


def _library_episode_label(item: LibraryWhisparrEpisode) -> str:
    """Build a human-readable log label for library Whisparr episodes."""
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - S{item.season_number:02d} - {item.episode_title}"
    return f"{series} - S{item.season_number:02d}"


def _library_season_context_label(item: LibraryWhisparrEpisode) -> str:
    """Build a log label for library Whisparr episode in season-context mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season_number:02d} (season-context)"


def adapt_upgrade(
    item: LibraryWhisparrEpisode,
    instance: Instance,
) -> SearchCandidate:
    """Convert a Whisparr library episode into a :class:`SearchCandidate` for upgrade.

    Respects ``instance.upgrade_whisparr_search_mode`` for episode vs season-context.
    No unreleased checks: upgrade items already have files.

    Args:
        item: A library episode from :meth:`WhisparrClient.get_episodes`.
        instance: The configured Whisparr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    episode_mode = instance.upgrade_whisparr_search_mode == WhisparrSearchMode.episode

    use_season_context = not episode_mode and item.series_id > 0 and item.season_number > 0

    if use_season_context:
        item_id = _season_item_id(item.series_id, item.season_number)
        label = _library_season_context_label(item)
        group_key: tuple[int, int] | None = (item.series_id, item.season_number)
        search_payload: dict[str, Any] = {
            "command": "SeasonSearch",
            "series_id": item.series_id,
            "season_number": item.season_number,
        }
    else:
        item_id = item.episode_id
        label = _library_episode_label(item)
        group_key = None
        search_payload = {
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        }

    return SearchCandidate(
        item_id=item_id,
        item_type="whisparr_episode",
        label=label,
        unreleased_reason=None,
        group_key=group_key,
        search_payload=search_payload,
    )


async def fetch_upgrade_pool(
    client: WhisparrClient,
    instance: Instance,
) -> list[LibraryWhisparrEpisode]:
    """Fetch and filter Whisparr library for upgrade-eligible episodes.

    Uses series rotation: fetches up to ``_UPGRADE_MAX_SERIES_PER_CYCLE``
    monitored series per cycle, starting from ``instance.upgrade_series_offset``.

    Args:
        client: An open :class:`WhisparrClient` context.
        instance: The configured Whisparr instance.

    Returns:
        List of upgrade-eligible :class:`LibraryWhisparrEpisode` items.
    """
    all_series = await client.get_series()
    monitored = sorted(
        [s for s in all_series if s.monitored],
        key=lambda s: s.id or 0,
    )

    if not monitored:
        return []

    offset = instance.upgrade_series_offset % len(monitored)
    selected = monitored[offset : offset + _UPGRADE_MAX_SERIES_PER_CYCLE]
    if len(selected) < _UPGRADE_MAX_SERIES_PER_CYCLE:
        remaining = _UPGRADE_MAX_SERIES_PER_CYCLE - len(selected)
        selected += monitored[:remaining]

    episodes: list[LibraryWhisparrEpisode] = []
    for s in selected:
        series_id = s.id or 0
        try:
            eps = await client.get_episodes(series_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[%s] failed to fetch episodes for series %d, skipping",
                instance.name,
                series_id,
            )
            continue
        episodes.extend(e for e in eps if e.monitored and e.has_file and e.cutoff_met)

    return episodes


async def dispatch_search(client: WhisparrClient, candidate: SearchCandidate) -> None:
    """Dispatch the appropriate Whisparr search command for *candidate*.

    Args:
        client: An open :class:`WhisparrClient` context.
        candidate: The candidate to search for.

    Raises:
        ValueError: If ``search_payload["command"]`` is unrecognised.
    """
    command = candidate.search_payload["command"]
    if command == "SeasonSearch":
        await client.search_season(
            candidate.search_payload["series_id"],
            candidate.search_payload["season_number"],
        )
    elif command == "EpisodeSearch":
        await client.search(candidate.search_payload["episode_id"])
    else:
        msg = f"Unknown Whisparr search command: {command}"
        raise ValueError(msg)


def make_client(instance: Instance) -> WhisparrClient:
    """Construct a :class:`WhisparrClient` for *instance*.

    Args:
        instance: The configured Whisparr instance.

    Returns:
        A new (unopened) :class:`WhisparrClient`.
    """
    return WhisparrClient(url=instance.url, api_key=instance.api_key)

"""Sonarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.sonarr.MissingEpisode` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.sonarr.SonarrClient`.
"""

from __future__ import annotations

import logging
from typing import Any

from houndarr.clients.sonarr import LibraryEpisode, MissingEpisode, SonarrClient
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
)
from houndarr.services.instances import Instance, SonarrSearchMode

logger = logging.getLogger(__name__)

_UPGRADE_MAX_SERIES_PER_CYCLE = 5

# ---------------------------------------------------------------------------
# Label builders (copied from search_loop.py; originals removed in Phase 2)
# ---------------------------------------------------------------------------


def _episode_label(item: MissingEpisode) -> str:
    """Build a human-readable log label for Sonarr episodes."""
    code = f"S{item.season:02d}E{item.episode:02d}"
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - {code} - {item.episode_title}"
    return f"{series} - {code}"


def _season_context_label(item: MissingEpisode) -> str:
    """Build a log label for Sonarr season-context search mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season:02d} (season-context)"


def _season_item_id(series_id: int, season_number: int) -> int:
    """Return a stable, negative synthetic ID representing a (series, season) pair.

    Season-context searches must be keyed on a season-level identity rather than
    an individual episode ID so that cooldown and log history remain consistent
    across cycles regardless of which episode happens to be the first candidate.

    The scheme encodes ``series_id`` and ``season_number`` as a single negative
    integer: ``-(series_id * 1000 + season_number)``.  Sonarr episode IDs are
    always positive, so there is no collision risk with real episode IDs stored
    in the same ``cooldowns``/``search_log`` tables.

    The multiplier 1000 supports up to 999 seasons per series, which exceeds
    any realistic Sonarr library.

    Args:
        series_id: Sonarr series ID (positive integer).
        season_number: Season number (0-based specials supported; positive for
            regular seasons).

    Returns:
        A unique negative integer that identifies this season across all cycles.
    """
    return -(series_id * 1000 + season_number)


def _sonarr_unreleased_reason(air_date_utc: str | None, grace_hrs: int) -> str | None:
    """Return skip reason when an episode should be treated as not yet searchable."""
    if _is_unreleased(air_date_utc):
        return "not yet released"
    if _is_within_post_release_grace(air_date_utc, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"
    return None


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Sonarr missing episode into a :class:`SearchCandidate`.

    Replicates the branching logic from ``search_loop.py`` for episode-mode
    versus season-context mode, including synthetic season ID generation,
    label selection, and unreleased-delay checking.

    Args:
        item: A missing episode returned by :meth:`SonarrClient.get_missing`.
        instance: The configured Sonarr instance (provides search-mode and
            unreleased-delay settings).

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    episode_mode = instance.sonarr_search_mode == SonarrSearchMode.episode

    use_season_context = not episode_mode and item.series_id is not None and item.season > 0

    if use_season_context:
        assert item.series_id is not None  # noqa: S101
        item_id = _season_item_id(item.series_id, item.season)
        label = _season_context_label(item)
        group_key: tuple[int, int] | None = (item.series_id, item.season)
        search_payload = {
            "command": "SeasonSearch",
            "series_id": item.series_id,
            "season_number": item.season,
        }
    else:
        item_id = item.episode_id
        label = _episode_label(item)
        group_key = None
        search_payload = {
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        }

    unreleased_reason = _sonarr_unreleased_reason(
        item.air_date_utc, instance.post_release_grace_hrs
    )

    return SearchCandidate(
        item_id=item_id,
        item_type="episode",
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=group_key,
        search_payload=search_payload,
    )


def adapt_cutoff(item: MissingEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Sonarr cutoff-unmet episode into a :class:`SearchCandidate`.

    The cutoff pass always uses episode-mode regardless of
    ``instance.sonarr_search_mode``, matching the current behavior in
    ``search_loop.py``.

    Args:
        item: A cutoff-unmet episode from :meth:`SonarrClient.get_cutoff_unmet`.
        instance: The configured Sonarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason = _sonarr_unreleased_reason(
        item.air_date_utc, instance.post_release_grace_hrs
    )

    return SearchCandidate(
        item_id=item.episode_id,
        item_type="episode",
        label=_episode_label(item),
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload={
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        },
    )


def _library_episode_label(item: LibraryEpisode) -> str:
    """Build a human-readable log label for library episodes."""
    code = f"S{item.season:02d}E{item.episode:02d}"
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - {code} - {item.episode_title}"
    return f"{series} - {code}"


def _library_season_context_label(item: LibraryEpisode) -> str:
    """Build a log label for library episode in season-context mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season:02d} (season-context)"


def adapt_upgrade(item: LibraryEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Sonarr library episode into a :class:`SearchCandidate` for upgrade.

    Respects ``instance.upgrade_sonarr_search_mode`` for episode vs season-context.
    No unreleased checks: upgrade items already have files.

    Args:
        item: A library episode from :meth:`SonarrClient.get_episodes`.
        instance: The configured Sonarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    episode_mode = instance.upgrade_sonarr_search_mode == SonarrSearchMode.episode

    use_season_context = not episode_mode and item.series_id > 0 and item.season > 0

    if use_season_context:
        item_id = _season_item_id(item.series_id, item.season)
        label = _library_season_context_label(item)
        group_key: tuple[int, int] | None = (item.series_id, item.season)
        search_payload: dict[str, Any] = {
            "command": "SeasonSearch",
            "series_id": item.series_id,
            "season_number": item.season,
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
        item_type="episode",
        label=label,
        unreleased_reason=None,
        group_key=group_key,
        search_payload=search_payload,
    )


async def fetch_upgrade_pool(
    client: SonarrClient,
    instance: Instance,
) -> list[LibraryEpisode]:
    """Fetch and filter Sonarr library for upgrade-eligible episodes.

    Uses series rotation: fetches up to ``_UPGRADE_MAX_SERIES_PER_CYCLE``
    monitored series per cycle, starting from ``instance.upgrade_series_offset``.

    Args:
        client: An open :class:`SonarrClient` context.
        instance: The configured Sonarr instance.

    Returns:
        List of upgrade-eligible :class:`LibraryEpisode` items.
    """
    all_series = await client.get_series()
    monitored = sorted(
        [s for s in all_series if s.get("monitored", False)],
        key=lambda s: s.get("id", 0),
    )

    if not monitored:
        return []

    offset = instance.upgrade_series_offset % len(monitored)
    selected = monitored[offset : offset + _UPGRADE_MAX_SERIES_PER_CYCLE]
    if len(selected) < _UPGRADE_MAX_SERIES_PER_CYCLE:
        remaining = _UPGRADE_MAX_SERIES_PER_CYCLE - len(selected)
        selected += monitored[:remaining]

    episodes: list[LibraryEpisode] = []
    for s in selected:
        series_id = s.get("id", 0)
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


async def dispatch_search(client: SonarrClient, candidate: SearchCandidate) -> None:
    """Dispatch the appropriate Sonarr search command for *candidate*.

    Reads ``candidate.search_payload["command"]`` to decide between
    ``EpisodeSearch`` and ``SeasonSearch``.

    Args:
        client: An open :class:`SonarrClient` context.
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
        msg = f"Unknown Sonarr search command: {command}"
        raise ValueError(msg)


def make_client(instance: Instance) -> SonarrClient:
    """Construct a :class:`SonarrClient` for *instance*.

    Args:
        instance: The configured Sonarr instance.

    Returns:
        A new (unopened) :class:`SonarrClient`.
    """
    return SonarrClient(url=instance.url, api_key=instance.api_key)

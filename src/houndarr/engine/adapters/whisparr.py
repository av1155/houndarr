"""Whisparr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.whisparr.MissingWhisparrEpisode` instances
into :class:`~houndarr.engine.candidates.SearchCandidate` and dispatches
search commands via :class:`~houndarr.clients.whisparr.WhisparrClient`.

Whisparr is a Sonarr fork, so the adapter structure mirrors the Sonarr adapter
with two key differences: the item type is ``whisparr_episode`` (not shared
with Sonarr), and episode labels omit ``episodeNumber`` (absent in Whisparr).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from houndarr.clients.whisparr import MissingWhisparrEpisode, WhisparrClient
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, WhisparrSearchMode

# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


def _episode_label(item: MissingWhisparrEpisode) -> str:
    """Build a human-readable log label for Whisparr episodes.

    Whisparr has no ``episodeNumber`` — labels use season only.
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

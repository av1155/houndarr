"""Lidarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.lidarr.MissingAlbum` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.lidarr.LidarrClient`.
"""

from __future__ import annotations

from houndarr.clients.lidarr import LidarrClient, MissingAlbum
from houndarr.engine.candidates import SearchCandidate, _is_within_unreleased_delay
from houndarr.services.instances import Instance, LidarrSearchMode

# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


def _album_label(item: MissingAlbum) -> str:
    """Build a human-readable log label for Lidarr albums."""
    artist = item.artist_name or "Unknown Artist"
    title = item.title or "Unknown Album"
    return f"{artist} - {title}"


def _artist_context_label(item: MissingAlbum) -> str:
    """Build a log label for Lidarr artist-context search mode."""
    artist = item.artist_name or "Unknown Artist"
    return f"{artist} (artist-context)"


def _artist_item_id(artist_id: int) -> int:
    """Return a stable, negative synthetic ID representing an artist.

    Artist-context searches are keyed on the artist level, analogous to
    Sonarr's season-context pattern.  The synthetic ID avoids collision with
    real album IDs (always positive).
    """
    return -(artist_id * 1000)


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingAlbum, instance: Instance) -> SearchCandidate:
    """Convert a Lidarr missing album into a :class:`SearchCandidate`.

    Args:
        item: A missing album returned by :meth:`LidarrClient.get_missing`.
        instance: The configured Lidarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    album_mode = instance.lidarr_search_mode == LidarrSearchMode.album

    use_artist_context = not album_mode and item.artist_id > 0

    if use_artist_context:
        item_id = _artist_item_id(item.artist_id)
        label = _artist_context_label(item)
        group_key: tuple[int, int] | None = (item.artist_id, 0)
        search_payload = {
            "command": "ArtistSearch",
            "artist_id": item.artist_id,
        }
    else:
        item_id = item.album_id
        label = _album_label(item)
        group_key = None
        search_payload = {
            "command": "AlbumSearch",
            "album_id": item.album_id,
        }

    unreleased_reason: str | None = (
        f"unreleased delay ({instance.unreleased_delay_hrs}h)"
        if _is_within_unreleased_delay(item.release_date, instance.unreleased_delay_hrs)
        else None
    )

    return SearchCandidate(
        item_id=item_id,
        item_type="album",
        label=label,
        unreleased_reason=unreleased_reason,
        group_key=group_key,
        search_payload=search_payload,
    )


def adapt_cutoff(item: MissingAlbum, instance: Instance) -> SearchCandidate:
    """Convert a Lidarr cutoff-unmet album into a :class:`SearchCandidate`.

    Cutoff always uses album-mode regardless of ``lidarr_search_mode``.

    Args:
        item: A cutoff-unmet album from :meth:`LidarrClient.get_cutoff_unmet`.
        instance: The configured Lidarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason: str | None = (
        f"unreleased delay ({instance.unreleased_delay_hrs}h)"
        if _is_within_unreleased_delay(item.release_date, instance.unreleased_delay_hrs)
        else None
    )

    return SearchCandidate(
        item_id=item.album_id,
        item_type="album",
        label=_album_label(item),
        unreleased_reason=unreleased_reason,
        group_key=None,
        search_payload={
            "command": "AlbumSearch",
            "album_id": item.album_id,
        },
    )


async def dispatch_search(client: LidarrClient, candidate: SearchCandidate) -> None:
    """Dispatch the appropriate Lidarr search command for *candidate*.

    Args:
        client: An open :class:`LidarrClient` context.
        candidate: The candidate to search for.

    Raises:
        ValueError: If ``search_payload["command"]`` is unrecognised.
    """
    command = candidate.search_payload["command"]
    if command == "ArtistSearch":
        await client.search_artist(candidate.search_payload["artist_id"])
    elif command == "AlbumSearch":
        await client.search(candidate.search_payload["album_id"])
    else:
        msg = f"Unknown Lidarr search command: {command}"
        raise ValueError(msg)


def make_client(instance: Instance) -> LidarrClient:
    """Construct a :class:`LidarrClient` for *instance*.

    Args:
        instance: The configured Lidarr instance.

    Returns:
        A new (unopened) :class:`LidarrClient`.
    """
    return LidarrClient(url=instance.url, api_key=instance.api_key)

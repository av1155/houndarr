"""Lidarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.lidarr.MissingAlbum` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.lidarr.LidarrClient`.
"""

from __future__ import annotations

import logging

from houndarr.clients.lidarr import LibraryAlbum, LidarrClient, MissingAlbum
from houndarr.engine.adapters._common import (
    ContextOverride,
    build_cutoff_candidate,
    build_missing_candidate,
)
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
)
from houndarr.services.instances import Instance, LidarrSearchMode

logger = logging.getLogger(__name__)

_UPGRADE_CUTOFF_EXCLUSION_MAX_PAGES = 10

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


def _lidarr_unreleased_reason(release_date: str | None, grace_hrs: int) -> str | None:
    """Return skip reason when an album should be treated as not yet searchable."""
    if _is_unreleased(release_date):
        return "not yet released"
    if _is_within_post_release_grace(release_date, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"
    return None


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
    unreleased_reason = _lidarr_unreleased_reason(
        item.release_date, instance.post_release_grace_hrs
    )

    context: ContextOverride | None = None
    if instance.lidarr_search_mode != LidarrSearchMode.album and item.artist_id > 0:
        context = ContextOverride(
            item_id=_artist_item_id(item.artist_id),
            label=_artist_context_label(item),
            group_key=(item.artist_id, 0),
            search_payload={
                "command": "ArtistSearch",
                "artist_id": item.artist_id,
            },
        )

    return build_missing_candidate(
        item_type="album",
        item_id=item.album_id,
        label=_album_label(item),
        unreleased_reason=unreleased_reason,
        search_payload={
            "command": "AlbumSearch",
            "album_id": item.album_id,
        },
        context=context,
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
    return build_cutoff_candidate(
        item_type="album",
        item_id=item.album_id,
        label=_album_label(item),
        unreleased_reason=_lidarr_unreleased_reason(
            item.release_date, instance.post_release_grace_hrs
        ),
        search_payload={
            "command": "AlbumSearch",
            "album_id": item.album_id,
        },
    )


def _library_album_label(item: LibraryAlbum) -> str:
    """Build a human-readable log label for library albums."""
    artist = item.artist_name or "Unknown Artist"
    title = item.title or "Unknown Album"
    return f"{artist} - {title}"


def _library_artist_context_label(item: LibraryAlbum) -> str:
    """Build a log label for library album in artist-context mode."""
    artist = item.artist_name or "Unknown Artist"
    return f"{artist} (artist-context)"


def adapt_upgrade(item: LibraryAlbum, instance: Instance) -> SearchCandidate:
    """Convert a Lidarr library album into a :class:`SearchCandidate` for upgrade.

    Respects ``instance.upgrade_lidarr_search_mode`` for album vs artist-context.
    No unreleased checks: upgrade items already have files.

    Args:
        item: A library album from :meth:`LidarrClient.get_albums`.
        instance: The configured Lidarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    album_mode = instance.upgrade_lidarr_search_mode == LidarrSearchMode.album

    use_artist_context = not album_mode and item.artist_id > 0

    if use_artist_context:
        item_id = _artist_item_id(item.artist_id)
        label = _library_artist_context_label(item)
        group_key: tuple[int, int] | None = (item.artist_id, 0)
        search_payload = {
            "command": "ArtistSearch",
            "artist_id": item.artist_id,
        }
    else:
        item_id = item.album_id
        label = _library_album_label(item)
        group_key = None
        search_payload = {
            "command": "AlbumSearch",
            "album_id": item.album_id,
        }

    return SearchCandidate(
        item_id=item_id,
        item_type="album",
        label=label,
        unreleased_reason=None,
        group_key=group_key,
        search_payload=search_payload,
    )


async def fetch_upgrade_pool(
    client: LidarrClient,
    instance: Instance,
) -> list[LibraryAlbum]:
    """Fetch and filter Lidarr library for upgrade-eligible albums.

    Builds a cutoff-unmet exclusion set by paginating ``wanted/cutoff``, then
    returns monitored albums with files that are NOT in the exclusion set.

    Args:
        client: An open :class:`LidarrClient` context.
        instance: The configured Lidarr instance.

    Returns:
        List of upgrade-eligible :class:`LibraryAlbum` items.
    """
    exclusion: set[int] = set()
    for page in range(1, _UPGRADE_CUTOFF_EXCLUSION_MAX_PAGES + 1):
        try:
            cutoff_items = await client.get_cutoff_unmet(page=page, page_size=250)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[%s] failed to fetch cutoff page %d for exclusion set",
                instance.name,
                page,
            )
            break
        for item in cutoff_items:
            exclusion.add(item.album_id)
        if len(cutoff_items) < 250:
            break

    library = await client.get_albums()
    return [a for a in library if a.monitored and a.has_file and a.album_id not in exclusion]


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


class LidarrAdapter:
    """Class-form Lidarr adapter for the :data:`ADAPTERS` registry.

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

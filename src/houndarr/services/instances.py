"""Instance CRUD service: create, read, update, and delete *arr instances.

API keys are never stored in plaintext.  Every write encrypts with the
caller-supplied Fernet *master_key*; every read decrypts transparently.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from enum import StrEnum
from typing import Any

from houndarr.config import (
    DEFAULT_ALLOWED_TIME_WINDOW,
    DEFAULT_BATCH_SIZE,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_BATCH_SIZE,
    DEFAULT_CUTOFF_COOLDOWN_DAYS,
    DEFAULT_CUTOFF_HOURLY_CAP,
    DEFAULT_HOURLY_CAP,
    DEFAULT_LIDARR_SEARCH_MODE,
    DEFAULT_POST_RELEASE_GRACE_HOURS,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_READARR_SEARCH_MODE,
    DEFAULT_SEARCH_ORDER,
    DEFAULT_SLEEP_INTERVAL_MINUTES,
    DEFAULT_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_BATCH_SIZE,
    DEFAULT_UPGRADE_COOLDOWN_DAYS,
    DEFAULT_UPGRADE_HOURLY_CAP,
    DEFAULT_UPGRADE_LIDARR_SEARCH_MODE,
    DEFAULT_UPGRADE_READARR_SEARCH_MODE,
    DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE,
    DEFAULT_WHISPARR_SEARCH_MODE,
)


class InstanceType(StrEnum):
    """Supported *arr application types."""

    radarr = "radarr"
    sonarr = "sonarr"
    lidarr = "lidarr"
    readarr = "readarr"
    whisparr_v2 = "whisparr_v2"
    whisparr_v3 = "whisparr_v3"


class SonarrSearchMode(StrEnum):
    """Supported Sonarr missing-search strategies."""

    episode = "episode"
    season_context = "season_context"


class LidarrSearchMode(StrEnum):
    """Supported Lidarr missing-search strategies."""

    album = "album"
    artist_context = "artist_context"


class ReadarrSearchMode(StrEnum):
    """Supported Readarr missing-search strategies."""

    book = "book"
    author_context = "author_context"


class WhisparrSearchMode(StrEnum):
    """Supported Whisparr missing-search strategies."""

    episode = "episode"
    season_context = "season_context"


class SearchOrder(StrEnum):
    """Order in which the engine iterates items within a search pass."""

    chronological = "chronological"
    random = "random"


@dataclass
class Instance:
    """In-memory representation of a configured *arr instance.

    ``api_key`` is always the **decrypted** plaintext value; the encrypted
    form is only ever kept in the database column ``encrypted_api_key``.
    """

    id: int
    name: str
    type: InstanceType
    url: str
    api_key: str
    enabled: bool
    batch_size: int
    sleep_interval_mins: int
    hourly_cap: int
    cooldown_days: int
    post_release_grace_hrs: int
    queue_limit: int
    cutoff_enabled: bool
    cutoff_batch_size: int
    cutoff_cooldown_days: int
    cutoff_hourly_cap: int
    created_at: str
    updated_at: str
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode
    upgrade_enabled: bool = False
    upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE
    upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS
    upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book
    upgrade_whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode
    upgrade_item_offset: int = 0
    upgrade_series_offset: int = 0
    missing_page_offset: int = 1
    cutoff_page_offset: int = 1
    allowed_time_window: str = ""
    search_order: SearchOrder = SearchOrder.chronological
    monitored_total: int = 0
    unreleased_count: int = 0
    snapshot_refreshed_at: str = ""


async def create_instance(
    *,
    master_key: bytes,
    name: str,
    type: InstanceType,  # noqa: A002
    url: str,
    api_key: str,
    enabled: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep_interval_mins: int = DEFAULT_SLEEP_INTERVAL_MINUTES,
    hourly_cap: int = DEFAULT_HOURLY_CAP,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    post_release_grace_hrs: int = DEFAULT_POST_RELEASE_GRACE_HOURS,
    queue_limit: int = DEFAULT_QUEUE_LIMIT,
    cutoff_enabled: bool = False,
    cutoff_batch_size: int = DEFAULT_CUTOFF_BATCH_SIZE,
    cutoff_cooldown_days: int = DEFAULT_CUTOFF_COOLDOWN_DAYS,
    cutoff_hourly_cap: int = DEFAULT_CUTOFF_HOURLY_CAP,
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE),
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE),
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE),
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode(DEFAULT_WHISPARR_SEARCH_MODE),
    upgrade_enabled: bool = False,
    upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE,
    upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS,
    upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP,
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(
        DEFAULT_UPGRADE_SONARR_SEARCH_MODE
    ),
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(
        DEFAULT_UPGRADE_LIDARR_SEARCH_MODE
    ),
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(
        DEFAULT_UPGRADE_READARR_SEARCH_MODE
    ),
    upgrade_whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode(
        DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE
    ),
    allowed_time_window: str = DEFAULT_ALLOWED_TIME_WINDOW,
    search_order: SearchOrder = SearchOrder(DEFAULT_SEARCH_ORDER),
) -> Instance:
    """Insert a new instance row and return the populated :class:`Instance`.

    Args:
        master_key: Fernet key used to encrypt *api_key* before storage.
        name: Human-readable label for the instance.
        type: One of the :class:`InstanceType` enum values.
        url: Base URL of the *arr instance (e.g. ``http://sonarr:8989``).
        api_key: Plaintext API key; will be encrypted before being written.
        enabled: Whether the search engine should process this instance.
        batch_size: Number of missing items to search per run.
        sleep_interval_mins: Minutes to sleep between search cycles.
        hourly_cap: Maximum searches allowed per hour.
        cooldown_days: Days to wait before re-searching the same item.
        post_release_grace_hrs: Hours to wait after release before searching.
        queue_limit: Skip search cycles when the download queue exceeds
            this count.  ``0`` disables the check.
        cutoff_enabled: Whether cutoff-unmet searching is active.
        cutoff_batch_size: Number of cutoff-unmet items per run.
        cutoff_cooldown_days: Days to wait before re-searching cutoff-unmet items.
        cutoff_hourly_cap: Maximum cutoff searches allowed per hour.
        sonarr_search_mode: Sonarr missing-search strategy mode.
        lidarr_search_mode: Lidarr missing-search strategy mode.
        readarr_search_mode: Readarr missing-search strategy mode.
        whisparr_search_mode: Whisparr missing-search strategy mode.
        upgrade_enabled: Whether upgrade searching is active.
        upgrade_batch_size: Number of upgrade items per run.
        upgrade_cooldown_days: Days to wait before re-searching upgrade items.
        upgrade_hourly_cap: Maximum upgrade searches allowed per hour.
        upgrade_sonarr_search_mode: Sonarr upgrade-search strategy mode.
        upgrade_lidarr_search_mode: Lidarr upgrade-search strategy mode.
        upgrade_readarr_search_mode: Readarr upgrade-search strategy mode.
        upgrade_whisparr_search_mode: Whisparr upgrade-search strategy mode.
        allowed_time_window: Optional schedule spec (e.g. ``"09:00-23:00"``)
            restricting scheduled cycles to configured windows.  Empty
            string disables the gate (24/7 operation).
        search_order: Order in which the engine iterates items within a
            pass.  ``chronological`` (default) preserves the legacy oldest
            first behaviour; ``random`` picks a random start page and
            shuffles items within each fetched page, and replaces the
            upgrade-pool offset rotation with a shuffle.

    Returns:
        The newly created :class:`Instance` with its database-assigned *id*.
    """
    from houndarr.repositories.instances import InstanceInsert
    from houndarr.repositories.instances import insert_instance as _repo_insert_instance

    payload = InstanceInsert(
        name=name,
        type=type,
        url=url,
        api_key=api_key,
        enabled=enabled,
        batch_size=batch_size,
        sleep_interval_mins=sleep_interval_mins,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        post_release_grace_hrs=post_release_grace_hrs,
        queue_limit=queue_limit,
        cutoff_enabled=cutoff_enabled,
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        sonarr_search_mode=sonarr_search_mode,
        lidarr_search_mode=lidarr_search_mode,
        readarr_search_mode=readarr_search_mode,
        whisparr_search_mode=whisparr_search_mode,
        upgrade_enabled=upgrade_enabled,
        upgrade_batch_size=upgrade_batch_size,
        upgrade_cooldown_days=upgrade_cooldown_days,
        upgrade_hourly_cap=upgrade_hourly_cap,
        upgrade_sonarr_search_mode=upgrade_sonarr_search_mode,
        upgrade_lidarr_search_mode=upgrade_lidarr_search_mode,
        upgrade_readarr_search_mode=upgrade_readarr_search_mode,
        upgrade_whisparr_search_mode=upgrade_whisparr_search_mode,
        allowed_time_window=allowed_time_window,
        search_order=search_order,
    )
    row_id = await _repo_insert_instance(payload, master_key=master_key)

    instance = await get_instance(row_id, master_key=master_key)
    assert instance is not None  # just inserted, cannot be None  # noqa: S101
    return instance


async def get_instance(id: int, *, master_key: bytes) -> Instance | None:  # noqa: A002
    """Fetch a single instance by *id*, or ``None`` if not found.

    Thin delegator over
    :func:`houndarr.repositories.instances.get_instance`.  The service
    keeps the historical ``id`` parameter name (shadowing a builtin,
    hence the ``# noqa: A002``) so existing callers do not move; the
    repository uses the unshadowed ``instance_id`` keyword matching
    the Protocol in :mod:`houndarr.protocols`.

    Args:
        id: Primary key of the instance row.
        master_key: Fernet key used to decrypt the stored API key.

    Returns:
        Decrypted :class:`Instance`, or ``None``.
    """
    from houndarr.repositories.instances import get_instance as _repo_get_instance

    return await _repo_get_instance(id, master_key=master_key)


async def list_instances(*, master_key: bytes) -> list[Instance]:
    """Return all instances ordered by ``id`` ascending.

    Thin delegator over
    :func:`houndarr.repositories.instances.list_instances`.

    Args:
        master_key: Fernet key used to decrypt each stored API key.

    Returns:
        List of decrypted :class:`Instance` objects (may be empty).
    """
    from houndarr.repositories.instances import list_instances as _repo_list_instances

    return await _repo_list_instances(master_key=master_key)


async def update_instance(
    id: int,  # noqa: A002
    *,
    master_key: bytes,
    **fields: Any,
) -> Instance | None:
    """Partially update an instance and return the refreshed :class:`Instance`.

    Thin delegator over
    :func:`houndarr.repositories.instances.update_instance`.  Accepts
    any subset of the mutable columns as kwargs to preserve the
    pre-D.4 call sites; unrecognised field names are silently ignored
    for safety and the remainder are packed into an
    :class:`~houndarr.repositories.instances.InstanceUpdate` payload.
    Payloads with no non-``None`` fields cause the repository to
    no-op, at which point this function still re-fetches and returns
    the current :class:`Instance` so the pre-refactor
    empty-update-returns-state contract stays intact.

    Args:
        id: Primary key of the instance to update.
        master_key: Fernet key used to re-encrypt ``api_key`` when
            that field is part of the update, and to decrypt on the
            return-value round trip.
        **fields: Column-value pairs to update.  See
            :class:`~houndarr.repositories.instances.InstanceUpdate`
            for the accepted keys; values that do not appear there
            are silently dropped.

    Returns:
        Updated :class:`Instance`, or ``None`` if *id* does not exist.
    """
    from houndarr.repositories.instances import InstanceUpdate
    from houndarr.repositories.instances import update_instance as _repo_update_instance

    allowed_field_names = {f.name for f in dataclass_fields(InstanceUpdate)}
    payload_kwargs = {name: value for name, value in fields.items() if name in allowed_field_names}
    payload = InstanceUpdate(**payload_kwargs)

    await _repo_update_instance(id, payload, master_key=master_key)
    return await get_instance(id, master_key=master_key)


async def delete_instance(id: int) -> bool:  # noqa: A002
    """Delete an instance row (cascade removes cooldowns).

    Thin delegator over
    :func:`houndarr.repositories.instances.delete_instance`.

    Args:
        id: Primary key of the instance to delete.

    Returns:
        ``True`` if a row was deleted, ``False`` if *id* did not exist.
    """
    from houndarr.repositories.instances import delete_instance as _repo_delete_instance

    return await _repo_delete_instance(id)


async def update_instance_snapshot(
    id: int,  # noqa: A002
    *,
    monitored_total: int,
    unreleased_count: int,
) -> None:
    """Atomically write the three snapshot columns for *id*.

    Thin delegator over
    :func:`houndarr.repositories.instances.update_instance_snapshot`.
    Called by the supervisor's ``refresh_instance_snapshots`` task.
    """
    from houndarr.repositories.instances import (
        update_instance_snapshot as _repo_update_instance_snapshot,
    )

    await _repo_update_instance_snapshot(
        id,
        monitored_total=monitored_total,
        unreleased_count=unreleased_count,
    )

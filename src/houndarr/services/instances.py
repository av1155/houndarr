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
    DEFAULT_UPGRADE_SERIES_WINDOW_SIZE,
    DEFAULT_UPGRADE_SONARR_SEARCH_MODE,
    DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE,
    DEFAULT_WHISPARR_V2_SEARCH_MODE,
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


class WhisparrV2SearchMode(StrEnum):
    """Supported Whisparr v2 missing-search strategies."""

    episode = "episode"
    season_context = "season_context"


class SearchOrder(StrEnum):
    """Order in which the engine iterates items within a search pass."""

    chronological = "chronological"
    random = "random"


@dataclass(frozen=True, slots=True)
class InstanceCore:
    """Row identity plus wire credentials for one configured *arr instance.

    These six fields uniquely identify an instance and describe how the
    engine reaches it.  They are kept apart from the policy sub-structs
    so callers that only need "which instance and how do I talk to it"
    (the client factory, the connection check, the snapshot writer) do
    not have to carry the full policy bag.

    ``api_key`` is always the **decrypted** plaintext value; the
    encrypted form only ever lives in the ``encrypted_api_key`` column.
    """

    id: int
    name: str
    type: InstanceType
    url: str
    api_key: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class MissingPolicy:
    """Tunables controlling one missing-search pass on an instance.

    Captures the rate shape (``batch_size`` / ``sleep_interval_mins`` /
    ``hourly_cap``), the per-item cooldown (``cooldown_days``), the
    post-release grace window (``post_release_grace_hrs``), the queue
    backpressure gate (``queue_limit``; ``0`` disables the check), and
    the per-app search-strategy mode for the four *arr variants that
    expose one (Sonarr, Lidarr, Readarr, Whisparr v2).  Radarr and
    Whisparr v3 have no strategy knob and so never read any of the
    mode fields.

    Defaults match :mod:`houndarr.config`; the field set matches the
    ``instances`` table columns written on fresh ``INSERT``.
    """

    batch_size: int = DEFAULT_BATCH_SIZE
    sleep_interval_mins: int = DEFAULT_SLEEP_INTERVAL_MINUTES
    hourly_cap: int = DEFAULT_HOURLY_CAP
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS
    post_release_grace_hrs: int = DEFAULT_POST_RELEASE_GRACE_HOURS
    queue_limit: int = DEFAULT_QUEUE_LIMIT
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE)
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE)
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE)
    whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode(
        DEFAULT_WHISPARR_V2_SEARCH_MODE
    )


@dataclass(frozen=True, slots=True)
class CutoffPolicy:
    """Tunables controlling one cutoff-unmet search pass.

    ``cutoff_enabled`` is the master switch; the three rate fields mirror
    :class:`MissingPolicy` at the cutoff cadence, which is typically much
    slower because cutoff-unmet is an optional polish pass, not the
    primary workload.  Cutoff is single-mode per app (no
    ``*_search_mode`` knobs) because the upstream *arr APIs expose a
    single cutoff endpoint per app.
    """

    cutoff_enabled: bool = False
    cutoff_batch_size: int = DEFAULT_CUTOFF_BATCH_SIZE
    cutoff_cooldown_days: int = DEFAULT_CUTOFF_COOLDOWN_DAYS
    cutoff_hourly_cap: int = DEFAULT_CUTOFF_HOURLY_CAP


@dataclass(frozen=True, slots=True)
class UpgradePolicy:
    """Tunables controlling one upgrade-search pass plus pool offsets.

    ``upgrade_enabled`` is the master switch; the three rate fields
    behave like :class:`MissingPolicy` but gate upgrade searches
    specifically.  Per-app ``upgrade_*_search_mode`` knobs parallel the
    missing-pass modes.

    ``upgrade_item_offset`` and ``upgrade_series_offset`` track
    library-pool rotation state: the engine fetches the upgrade pool
    from the *arr library and rotates through it across cycles so a
    large library is explored evenly instead of always retrying the
    same head.  The offsets live here rather than in
    :class:`SchedulePolicy` because they are intrinsic to how the
    upgrade pool is constructed, not to when or in what order the
    cycle runs.
    """

    upgrade_enabled: bool = False
    upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE
    upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS
    upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode(
        DEFAULT_UPGRADE_SONARR_SEARCH_MODE
    )
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode(
        DEFAULT_UPGRADE_LIDARR_SEARCH_MODE
    )
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode(
        DEFAULT_UPGRADE_READARR_SEARCH_MODE
    )
    upgrade_whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode(
        DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE
    )
    upgrade_item_offset: int = 0
    upgrade_series_offset: int = 0
    upgrade_series_window_size: int = DEFAULT_UPGRADE_SERIES_WINDOW_SIZE


@dataclass(frozen=True, slots=True)
class SchedulePolicy:
    """When the engine runs and in what order it walks items.

    ``allowed_time_window`` is an optional schedule spec (e.g.
    ``"09:00-23:00"``) that gates scheduled cycles; the empty string
    disables the gate and runs 24/7.  ``search_order`` selects
    ``chronological`` (legacy; oldest-first) or ``random`` (shuffle
    within each page, random start page).

    ``missing_page_offset`` and ``cutoff_page_offset`` rotate the
    *arr ``/wanted`` pagination across cycles so the pool is explored
    evenly rather than always re-walking page 1.  They begin at 1
    (the *arr APIs are 1-indexed) and wrap when the probe reports
    no more items.
    """

    allowed_time_window: str = DEFAULT_ALLOWED_TIME_WINDOW
    search_order: SearchOrder = SearchOrder(DEFAULT_SEARCH_ORDER)
    missing_page_offset: int = 1
    cutoff_page_offset: int = 1


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Refreshable telemetry written by the supervisor for the dashboard.

    The supervisor's ``refresh_instance_snapshots`` task writes these
    three columns periodically; the dashboard reads them to render
    per-instance headline counts without issuing a fresh *arr call on
    every page load.  They carry no search-policy meaning and so live
    apart from the three policy sub-structs above.

    ``snapshot_refreshed_at`` is an ISO-8601 UTC timestamp or the
    empty string when no refresh has run yet (first boot, or pre-v13
    migration).
    """

    monitored_total: int = 0
    unreleased_count: int = 0
    snapshot_refreshed_at: str = ""


@dataclass(frozen=True, slots=True)
class InstanceTimestamps:
    """Row-level audit timestamps.

    ``created_at`` and ``updated_at`` are written by SQLite's column
    defaults (``DEFAULT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')``) and
    bumped by the repository's update path.  They are required here
    because a deserialised instance always knows both values; the
    sub-struct rejects partial construction on purpose.
    """

    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Instance:
    """In-memory representation of a configured *arr instance.

    Composes seven sub-structs that partition the row into coherent
    policy groups: :class:`InstanceCore` for identity and wire
    credentials, :class:`MissingPolicy` / :class:`CutoffPolicy` /
    :class:`UpgradePolicy` for the three search passes,
    :class:`SchedulePolicy` for when and in what order the engine
    runs, :class:`RuntimeSnapshot` for dashboard telemetry, and
    :class:`InstanceTimestamps` for audit metadata.

    :class:`Instance` is frozen.  Offset rotations and snapshot
    updates travel through the repository (``update_instance`` or
    ``update_instance_snapshot``) and surface as a freshly-fetched
    :class:`Instance`; no call site mutates the in-memory record in
    place.  Each sub-struct is itself frozen, so field-level writes
    are also blocked.  Callers that need a modified copy compose
    :func:`dataclasses.replace`::

        instance = dataclasses.replace(
            instance, missing=dataclasses.replace(instance.missing, batch_size=1)
        )

    ``slots=True`` keeps per-instance memory tight and blocks stray
    attribute assignment on the facade.  Combined with ``frozen=True``
    the only way to evolve state is by constructing a new value
    object, which makes the repository the single mutation authority.

    API keys are always decrypted at this layer:
    ``instance.core.api_key`` returns plaintext; the encrypted form
    only ever lives in the database column ``encrypted_api_key``.
    """

    core: InstanceCore
    missing: MissingPolicy
    cutoff: CutoffPolicy
    upgrade: UpgradePolicy
    schedule: SchedulePolicy
    snapshot: RuntimeSnapshot
    timestamps: InstanceTimestamps


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
    whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode(
        DEFAULT_WHISPARR_V2_SEARCH_MODE
    ),
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
    upgrade_whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode(
        DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE
    ),
    upgrade_series_window_size: int = DEFAULT_UPGRADE_SERIES_WINDOW_SIZE,
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
        whisparr_v2_search_mode: Whisparr v2 missing-search strategy mode.
        upgrade_enabled: Whether upgrade searching is active.
        upgrade_batch_size: Number of upgrade items per run.
        upgrade_cooldown_days: Days to wait before re-searching upgrade items.
        upgrade_hourly_cap: Maximum upgrade searches allowed per hour.
        upgrade_sonarr_search_mode: Sonarr upgrade-search strategy mode.
        upgrade_lidarr_search_mode: Lidarr upgrade-search strategy mode.
        upgrade_readarr_search_mode: Readarr upgrade-search strategy mode.
        upgrade_whisparr_v2_search_mode: Whisparr v2 upgrade-search strategy mode.
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
        whisparr_v2_search_mode=whisparr_v2_search_mode,
        upgrade_enabled=upgrade_enabled,
        upgrade_batch_size=upgrade_batch_size,
        upgrade_cooldown_days=upgrade_cooldown_days,
        upgrade_hourly_cap=upgrade_hourly_cap,
        upgrade_sonarr_search_mode=upgrade_sonarr_search_mode,
        upgrade_lidarr_search_mode=upgrade_lidarr_search_mode,
        upgrade_readarr_search_mode=upgrade_readarr_search_mode,
        upgrade_whisparr_v2_search_mode=upgrade_whisparr_v2_search_mode,
        upgrade_series_window_size=upgrade_series_window_size,
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


async def active_error_instance_ids() -> set[int]:
    """Return the set of instance IDs whose newest ``search_log`` row is an error.

    Thin delegator over
    :func:`houndarr.repositories.search_log.fetch_active_error_instance_ids`
    since D.27.  The service layer exposes this for the settings
    page and dashboard consumers; the repository owns the windowed
    ``ROW_NUMBER()`` SQL.  See the repository function's docstring
    for the reasoning behind the 48-hour window and the
    ``strftime`` cutoff format.
    """
    from houndarr.repositories.search_log import fetch_active_error_instance_ids

    return await fetch_active_error_instance_ids()


async def update_instance(
    id: int,  # noqa: A002
    *,
    master_key: bytes,
    **fields: Any,
) -> Instance | None:
    """Partially update an instance and return the refreshed :class:`Instance`.

    Thin delegator over
    :func:`houndarr.repositories.instances.update_instance`.  Kwargs
    are packed into an
    :class:`~houndarr.repositories.instances.InstanceUpdate` payload
    and forwarded.  Payloads with no non-``None`` fields cause the
    repository to no-op, at which point this function still re-
    fetches and returns the current :class:`Instance` so the pre-
    refactor empty-update-returns-state contract stays intact.

    Unknown kwargs raise :class:`TypeError`.  Until the review-
    adversarial pass that landed with this change the surface
    silently dropped unrecognised names "for safety"; that made
    rename drift (either a repository column rename or a caller-
    side typo) a silent bug farm where the SQL update ran with
    fewer fields than the caller expected.  Raising surfaces the
    drift at the call site instead, and every production caller
    already passes only valid column names.

    Args:
        id: Primary key of the instance to update.
        master_key: Fernet key used to re-encrypt ``api_key`` when
            that field is part of the update, and to decrypt on the
            return-value round trip.
        **fields: Column-value pairs to update.  Every key must be a
            field name on
            :class:`~houndarr.repositories.instances.InstanceUpdate`;
            unknown names raise :class:`TypeError` with the offending
            key set named explicitly.

    Returns:
        Updated :class:`Instance`, or ``None`` if *id* does not exist.

    Raises:
        TypeError: When *fields* contains a key that is not a valid
            :class:`InstanceUpdate` column.
    """
    from houndarr.repositories.instances import InstanceUpdate
    from houndarr.repositories.instances import update_instance as _repo_update_instance

    allowed_field_names = {f.name for f in dataclass_fields(InstanceUpdate)}
    unknown = fields.keys() - allowed_field_names
    if unknown:
        raise TypeError("update_instance received unknown field(s): " + ", ".join(sorted(unknown)))
    payload = InstanceUpdate(**fields)

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

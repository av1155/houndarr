"""Instance CRUD service: create, read, update, and delete *arr instances.

API keys are never stored in plaintext.  Every write encrypts with the
caller-supplied Fernet *master_key*; every read decrypts transparently.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    expose one (Sonarr, Lidarr, Readarr, Whisparr).  Radarr has no
    strategy knob and so never reads any of the mode fields.

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
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode(DEFAULT_WHISPARR_SEARCH_MODE)


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
    upgrade_whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode(
        DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE
    )
    upgrade_item_offset: int = 0
    upgrade_series_offset: int = 0


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


@dataclass(init=False)
class Instance:
    """In-memory representation of a configured *arr instance.

    Composes seven frozen sub-structs (:class:`InstanceCore`,
    :class:`MissingPolicy`, :class:`CutoffPolicy`, :class:`UpgradePolicy`,
    :class:`SchedulePolicy`, :class:`RuntimeSnapshot`,
    :class:`InstanceTimestamps`) and exposes every flat attribute of
    the pre-refactor dataclass via ``@property`` delegation.  Both
    access patterns work: ``instance.batch_size`` reads through to
    ``instance.missing.batch_size``, and assignment to
    ``instance.batch_size`` rebuilds the :class:`MissingPolicy`
    sub-struct via :func:`dataclasses.replace`.

    The constructor still accepts the 39 flat keyword arguments the
    pre-refactor dataclass accepted so every caller site keeps working
    through the caller-migration batches.  The flat property
    delegators and the flat-kwargs ``__init__`` are both removed in
    :ref:`D.20 <track-d>` once every caller has switched to sub-struct
    access (``instance.missing.batch_size``) and sub-struct
    construction (``Instance(core=..., missing=...)``).

    ``api_key`` is always the **decrypted** plaintext value; the
    encrypted form only ever lives in the database column
    ``encrypted_api_key``.  :class:`Instance` stays mutable
    (non-frozen) deliberately: the skip-log cache and test fixtures
    rely on in-place attribute writes, and the slots audit records
    this as a known exception.
    """

    core: InstanceCore
    missing: MissingPolicy
    cutoff: CutoffPolicy
    upgrade: UpgradePolicy
    schedule: SchedulePolicy
    snapshot: RuntimeSnapshot
    timestamps: InstanceTimestamps

    def __init__(  # noqa: PLR0913
        self,
        *,
        id: int,  # noqa: A002
        name: str,
        type: InstanceType,  # noqa: A002
        url: str,
        api_key: str,
        enabled: bool,
        batch_size: int,
        sleep_interval_mins: int,
        hourly_cap: int,
        cooldown_days: int,
        post_release_grace_hrs: int,
        queue_limit: int,
        cutoff_enabled: bool,
        cutoff_batch_size: int,
        cutoff_cooldown_days: int,
        cutoff_hourly_cap: int,
        created_at: str,
        updated_at: str,
        sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode,
        lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album,
        readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book,
        whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode,
        upgrade_enabled: bool = False,
        upgrade_batch_size: int = DEFAULT_UPGRADE_BATCH_SIZE,
        upgrade_cooldown_days: int = DEFAULT_UPGRADE_COOLDOWN_DAYS,
        upgrade_hourly_cap: int = DEFAULT_UPGRADE_HOURLY_CAP,
        upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode,
        upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album,
        upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book,
        upgrade_whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode,
        upgrade_item_offset: int = 0,
        upgrade_series_offset: int = 0,
        missing_page_offset: int = 1,
        cutoff_page_offset: int = 1,
        allowed_time_window: str = "",
        search_order: SearchOrder = SearchOrder.chronological,
        monitored_total: int = 0,
        unreleased_count: int = 0,
        snapshot_refreshed_at: str = "",
    ) -> None:
        """Accept the pre-refactor 39-kwarg shape and build sub-structs.

        Defaults match the pre-refactor :class:`Instance` dataclass
        field-by-field so byte-level behaviour is preserved for every
        caller that does not pass a given kwarg.  The sub-struct
        defaults (which route through :mod:`houndarr.config` constants)
        apply only when a sub-struct is constructed directly; the
        Instance facade takes its flat-kwarg defaults as the source of
        truth to avoid silently changing what ``Instance(...)`` without
        ``search_order`` resolves to mid-refactor.
        """
        self.core = InstanceCore(
            id=id,
            name=name,
            type=type,
            url=url,
            api_key=api_key,
            enabled=enabled,
        )
        self.missing = MissingPolicy(
            batch_size=batch_size,
            sleep_interval_mins=sleep_interval_mins,
            hourly_cap=hourly_cap,
            cooldown_days=cooldown_days,
            post_release_grace_hrs=post_release_grace_hrs,
            queue_limit=queue_limit,
            sonarr_search_mode=sonarr_search_mode,
            lidarr_search_mode=lidarr_search_mode,
            readarr_search_mode=readarr_search_mode,
            whisparr_search_mode=whisparr_search_mode,
        )
        self.cutoff = CutoffPolicy(
            cutoff_enabled=cutoff_enabled,
            cutoff_batch_size=cutoff_batch_size,
            cutoff_cooldown_days=cutoff_cooldown_days,
            cutoff_hourly_cap=cutoff_hourly_cap,
        )
        self.upgrade = UpgradePolicy(
            upgrade_enabled=upgrade_enabled,
            upgrade_batch_size=upgrade_batch_size,
            upgrade_cooldown_days=upgrade_cooldown_days,
            upgrade_hourly_cap=upgrade_hourly_cap,
            upgrade_sonarr_search_mode=upgrade_sonarr_search_mode,
            upgrade_lidarr_search_mode=upgrade_lidarr_search_mode,
            upgrade_readarr_search_mode=upgrade_readarr_search_mode,
            upgrade_whisparr_search_mode=upgrade_whisparr_search_mode,
            upgrade_item_offset=upgrade_item_offset,
            upgrade_series_offset=upgrade_series_offset,
        )
        self.schedule = SchedulePolicy(
            allowed_time_window=allowed_time_window,
            search_order=search_order,
            missing_page_offset=missing_page_offset,
            cutoff_page_offset=cutoff_page_offset,
        )
        self.snapshot = RuntimeSnapshot(
            monitored_total=monitored_total,
            unreleased_count=unreleased_count,
            snapshot_refreshed_at=snapshot_refreshed_at,
        )
        self.timestamps = InstanceTimestamps(
            created_at=created_at,
            updated_at=updated_at,
        )

    # InstanceCore delegation.

    @property
    def id(self) -> int:  # noqa: A003
        return self.core.id

    @id.setter
    def id(self, value: int) -> None:
        self.core = replace(self.core, id=value)

    @property
    def name(self) -> str:
        return self.core.name

    @name.setter
    def name(self, value: str) -> None:
        self.core = replace(self.core, name=value)

    @property
    def type(self) -> InstanceType:  # noqa: A003
        return self.core.type

    @type.setter
    def type(self, value: InstanceType) -> None:
        self.core = replace(self.core, type=value)

    @property
    def url(self) -> str:
        return self.core.url

    @url.setter
    def url(self, value: str) -> None:
        self.core = replace(self.core, url=value)

    @property
    def api_key(self) -> str:
        return self.core.api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.core = replace(self.core, api_key=value)

    @property
    def enabled(self) -> bool:
        return self.core.enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self.core = replace(self.core, enabled=value)

    # MissingPolicy delegation.

    @property
    def batch_size(self) -> int:
        return self.missing.batch_size

    @batch_size.setter
    def batch_size(self, value: int) -> None:
        self.missing = replace(self.missing, batch_size=value)

    @property
    def sleep_interval_mins(self) -> int:
        return self.missing.sleep_interval_mins

    @sleep_interval_mins.setter
    def sleep_interval_mins(self, value: int) -> None:
        self.missing = replace(self.missing, sleep_interval_mins=value)

    @property
    def hourly_cap(self) -> int:
        return self.missing.hourly_cap

    @hourly_cap.setter
    def hourly_cap(self, value: int) -> None:
        self.missing = replace(self.missing, hourly_cap=value)

    @property
    def cooldown_days(self) -> int:
        return self.missing.cooldown_days

    @cooldown_days.setter
    def cooldown_days(self, value: int) -> None:
        self.missing = replace(self.missing, cooldown_days=value)

    @property
    def post_release_grace_hrs(self) -> int:
        return self.missing.post_release_grace_hrs

    @post_release_grace_hrs.setter
    def post_release_grace_hrs(self, value: int) -> None:
        self.missing = replace(self.missing, post_release_grace_hrs=value)

    @property
    def queue_limit(self) -> int:
        return self.missing.queue_limit

    @queue_limit.setter
    def queue_limit(self, value: int) -> None:
        self.missing = replace(self.missing, queue_limit=value)

    @property
    def sonarr_search_mode(self) -> SonarrSearchMode:
        return self.missing.sonarr_search_mode

    @sonarr_search_mode.setter
    def sonarr_search_mode(self, value: SonarrSearchMode) -> None:
        self.missing = replace(self.missing, sonarr_search_mode=value)

    @property
    def lidarr_search_mode(self) -> LidarrSearchMode:
        return self.missing.lidarr_search_mode

    @lidarr_search_mode.setter
    def lidarr_search_mode(self, value: LidarrSearchMode) -> None:
        self.missing = replace(self.missing, lidarr_search_mode=value)

    @property
    def readarr_search_mode(self) -> ReadarrSearchMode:
        return self.missing.readarr_search_mode

    @readarr_search_mode.setter
    def readarr_search_mode(self, value: ReadarrSearchMode) -> None:
        self.missing = replace(self.missing, readarr_search_mode=value)

    @property
    def whisparr_search_mode(self) -> WhisparrSearchMode:
        return self.missing.whisparr_search_mode

    @whisparr_search_mode.setter
    def whisparr_search_mode(self, value: WhisparrSearchMode) -> None:
        self.missing = replace(self.missing, whisparr_search_mode=value)

    # CutoffPolicy delegation.

    @property
    def cutoff_enabled(self) -> bool:
        return self.cutoff.cutoff_enabled

    @cutoff_enabled.setter
    def cutoff_enabled(self, value: bool) -> None:
        self.cutoff = replace(self.cutoff, cutoff_enabled=value)

    @property
    def cutoff_batch_size(self) -> int:
        return self.cutoff.cutoff_batch_size

    @cutoff_batch_size.setter
    def cutoff_batch_size(self, value: int) -> None:
        self.cutoff = replace(self.cutoff, cutoff_batch_size=value)

    @property
    def cutoff_cooldown_days(self) -> int:
        return self.cutoff.cutoff_cooldown_days

    @cutoff_cooldown_days.setter
    def cutoff_cooldown_days(self, value: int) -> None:
        self.cutoff = replace(self.cutoff, cutoff_cooldown_days=value)

    @property
    def cutoff_hourly_cap(self) -> int:
        return self.cutoff.cutoff_hourly_cap

    @cutoff_hourly_cap.setter
    def cutoff_hourly_cap(self, value: int) -> None:
        self.cutoff = replace(self.cutoff, cutoff_hourly_cap=value)

    # UpgradePolicy delegation.

    @property
    def upgrade_enabled(self) -> bool:
        return self.upgrade.upgrade_enabled

    @upgrade_enabled.setter
    def upgrade_enabled(self, value: bool) -> None:
        self.upgrade = replace(self.upgrade, upgrade_enabled=value)

    @property
    def upgrade_batch_size(self) -> int:
        return self.upgrade.upgrade_batch_size

    @upgrade_batch_size.setter
    def upgrade_batch_size(self, value: int) -> None:
        self.upgrade = replace(self.upgrade, upgrade_batch_size=value)

    @property
    def upgrade_cooldown_days(self) -> int:
        return self.upgrade.upgrade_cooldown_days

    @upgrade_cooldown_days.setter
    def upgrade_cooldown_days(self, value: int) -> None:
        self.upgrade = replace(self.upgrade, upgrade_cooldown_days=value)

    @property
    def upgrade_hourly_cap(self) -> int:
        return self.upgrade.upgrade_hourly_cap

    @upgrade_hourly_cap.setter
    def upgrade_hourly_cap(self, value: int) -> None:
        self.upgrade = replace(self.upgrade, upgrade_hourly_cap=value)

    @property
    def upgrade_sonarr_search_mode(self) -> SonarrSearchMode:
        return self.upgrade.upgrade_sonarr_search_mode

    @upgrade_sonarr_search_mode.setter
    def upgrade_sonarr_search_mode(self, value: SonarrSearchMode) -> None:
        self.upgrade = replace(self.upgrade, upgrade_sonarr_search_mode=value)

    @property
    def upgrade_lidarr_search_mode(self) -> LidarrSearchMode:
        return self.upgrade.upgrade_lidarr_search_mode

    @upgrade_lidarr_search_mode.setter
    def upgrade_lidarr_search_mode(self, value: LidarrSearchMode) -> None:
        self.upgrade = replace(self.upgrade, upgrade_lidarr_search_mode=value)

    @property
    def upgrade_readarr_search_mode(self) -> ReadarrSearchMode:
        return self.upgrade.upgrade_readarr_search_mode

    @upgrade_readarr_search_mode.setter
    def upgrade_readarr_search_mode(self, value: ReadarrSearchMode) -> None:
        self.upgrade = replace(self.upgrade, upgrade_readarr_search_mode=value)

    @property
    def upgrade_whisparr_search_mode(self) -> WhisparrSearchMode:
        return self.upgrade.upgrade_whisparr_search_mode

    @upgrade_whisparr_search_mode.setter
    def upgrade_whisparr_search_mode(self, value: WhisparrSearchMode) -> None:
        self.upgrade = replace(self.upgrade, upgrade_whisparr_search_mode=value)

    @property
    def upgrade_item_offset(self) -> int:
        return self.upgrade.upgrade_item_offset

    @upgrade_item_offset.setter
    def upgrade_item_offset(self, value: int) -> None:
        self.upgrade = replace(self.upgrade, upgrade_item_offset=value)

    @property
    def upgrade_series_offset(self) -> int:
        return self.upgrade.upgrade_series_offset

    @upgrade_series_offset.setter
    def upgrade_series_offset(self, value: int) -> None:
        self.upgrade = replace(self.upgrade, upgrade_series_offset=value)

    # SchedulePolicy delegation.

    @property
    def allowed_time_window(self) -> str:
        return self.schedule.allowed_time_window

    @allowed_time_window.setter
    def allowed_time_window(self, value: str) -> None:
        self.schedule = replace(self.schedule, allowed_time_window=value)

    @property
    def search_order(self) -> SearchOrder:
        return self.schedule.search_order

    @search_order.setter
    def search_order(self, value: SearchOrder) -> None:
        self.schedule = replace(self.schedule, search_order=value)

    @property
    def missing_page_offset(self) -> int:
        return self.schedule.missing_page_offset

    @missing_page_offset.setter
    def missing_page_offset(self, value: int) -> None:
        self.schedule = replace(self.schedule, missing_page_offset=value)

    @property
    def cutoff_page_offset(self) -> int:
        return self.schedule.cutoff_page_offset

    @cutoff_page_offset.setter
    def cutoff_page_offset(self, value: int) -> None:
        self.schedule = replace(self.schedule, cutoff_page_offset=value)

    # RuntimeSnapshot delegation.

    @property
    def monitored_total(self) -> int:
        return self.snapshot.monitored_total

    @monitored_total.setter
    def monitored_total(self, value: int) -> None:
        self.snapshot = replace(self.snapshot, monitored_total=value)

    @property
    def unreleased_count(self) -> int:
        return self.snapshot.unreleased_count

    @unreleased_count.setter
    def unreleased_count(self, value: int) -> None:
        self.snapshot = replace(self.snapshot, unreleased_count=value)

    @property
    def snapshot_refreshed_at(self) -> str:
        return self.snapshot.snapshot_refreshed_at

    @snapshot_refreshed_at.setter
    def snapshot_refreshed_at(self, value: str) -> None:
        self.snapshot = replace(self.snapshot, snapshot_refreshed_at=value)

    # InstanceTimestamps delegation.

    @property
    def created_at(self) -> str:
        return self.timestamps.created_at

    @created_at.setter
    def created_at(self, value: str) -> None:
        self.timestamps = replace(self.timestamps, created_at=value)

    @property
    def updated_at(self) -> str:
        return self.timestamps.updated_at

    @updated_at.setter
    def updated_at(self, value: str) -> None:
        self.timestamps = replace(self.timestamps, updated_at=value)


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

"""Pinning tests for the :class:`Instance` policy sub-struct dataclasses.

Track D.13 declares seven frozen, slotted dataclasses alongside the
existing :class:`Instance` dataclass:
:class:`InstanceCore`, :class:`MissingPolicy`, :class:`CutoffPolicy`,
:class:`UpgradePolicy`, :class:`SchedulePolicy`, :class:`RuntimeSnapshot`,
and :class:`InstanceTimestamps`.  The declarations are currently unused;
D.14 wraps :class:`Instance` as a facade that composes them via
``@property`` delegation, and D.15 through D.19 migrate the callers.
D.20 removes the flat-attribute delegation.

These tests lock the contract that every later batch keys off:

* each sub-struct is a ``@dataclass(frozen=True, slots=True)``
* each sub-struct exposes the exact field names the plan specifies
* default values match the constants in :mod:`houndarr.config` so fresh
  construction never drifts from what :class:`InstanceInsert` writes
* the seven sub-struct field sets partition :class:`Instance`'s 39
  fields disjointly and exhaustively

Every assertion below has to stay green through D.14 - D.20.  A test
failing here means the sub-struct shape has drifted from the plan and
the facade migration is about to propagate the drift.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

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
    DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE,
    DEFAULT_WHISPARR_V2_SEARCH_MODE,
)
from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    LidarrSearchMode,
    MissingPolicy,
    ReadarrSearchMode,
    RuntimeSnapshot,
    SchedulePolicy,
    SearchOrder,
    SonarrSearchMode,
    UpgradePolicy,
    WhisparrV2SearchMode,
)

pytestmark = pytest.mark.pinning


SUBSTRUCTS: list[type] = [
    InstanceCore,
    MissingPolicy,
    CutoffPolicy,
    UpgradePolicy,
    SchedulePolicy,
    RuntimeSnapshot,
    InstanceTimestamps,
]


def _field_names(cls: type) -> list[str]:
    """Return the dataclass field names of *cls* in declaration order."""
    return [f.name for f in dataclasses.fields(cls)]


def _field_defaults(cls: type) -> dict[str, Any]:
    """Return a mapping from field name to default value.

    Fields with no default (required fields) are skipped.  Fields that
    use ``default_factory`` are also skipped, but none of the D.13
    sub-structs use factories so the omission is moot today.
    """
    defaults: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            defaults[f.name] = f.default
    return defaults


# Structural invariants shared by every sub-struct.


@pytest.mark.parametrize("cls", SUBSTRUCTS)
def test_substruct_is_frozen_slotted_dataclass(cls: type) -> None:
    """Every sub-struct is a frozen, slotted dataclass."""
    assert dataclasses.is_dataclass(cls), f"{cls.__name__} is not a dataclass"
    params = cls.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen is True, f"{cls.__name__} is not frozen"
    assert "__slots__" in cls.__dict__, f"{cls.__name__} does not declare __slots__"


@pytest.mark.parametrize("cls", SUBSTRUCTS)
def test_substruct_rejects_attribute_assignment(cls: type) -> None:
    """Frozen sub-structs raise ``FrozenInstanceError`` on setattr."""
    instance = _construct_with_required(cls)
    first_field = dataclasses.fields(cls)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, first_field, getattr(instance, first_field))


@pytest.mark.parametrize("cls", SUBSTRUCTS)
def test_substruct_rejects_unknown_attribute(cls: type) -> None:
    """Slotted sub-structs reject attribute names outside ``__slots__``."""
    instance = _construct_with_required(cls)
    with pytest.raises(AttributeError):
        object.__setattr__(instance, "not_a_real_field", 42)


def _construct_with_required(cls: type) -> Any:
    """Build a minimal instance of *cls*, filling required fields.

    Used by the frozen / slots invariant tests so they do not have to
    know each sub-struct's required-field surface separately.  Every
    required field is typed by the sub-struct declarations, so a small
    dispatch table suffices.
    """
    required: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            continue
        if f.type is int or f.type == "int":
            required[f.name] = 0
        elif f.type is str or f.type == "str":
            required[f.name] = ""
        elif f.type is bool or f.type == "bool":
            required[f.name] = False
        elif f.type is InstanceType or f.type == "InstanceType":
            required[f.name] = InstanceType.sonarr
        else:
            raise AssertionError(
                f"Required field {cls.__name__}.{f.name} has an unhandled type {f.type!r};"
                " extend _construct_with_required to cover it."
            )
    return cls(**required)


# InstanceCore.


def test_instance_core_fields_in_declaration_order() -> None:
    """InstanceCore exposes the six identity fields in the locked order."""
    assert _field_names(InstanceCore) == [
        "id",
        "name",
        "type",
        "url",
        "api_key",
        "enabled",
    ]


def test_instance_core_defaults() -> None:
    """Only ``enabled`` carries a default; the other five are required."""
    assert _field_defaults(InstanceCore) == {"enabled": True}


# MissingPolicy.


def test_missing_policy_fields_in_declaration_order() -> None:
    """MissingPolicy exposes the ten missing-pass tunables in order."""
    assert _field_names(MissingPolicy) == [
        "batch_size",
        "sleep_interval_mins",
        "hourly_cap",
        "cooldown_days",
        "post_release_grace_hrs",
        "queue_limit",
        "sonarr_search_mode",
        "lidarr_search_mode",
        "readarr_search_mode",
        "whisparr_v2_search_mode",
    ]


def test_missing_policy_defaults_match_config() -> None:
    """Every MissingPolicy default flows from :mod:`houndarr.config`."""
    policy = MissingPolicy()
    assert policy.batch_size == DEFAULT_BATCH_SIZE
    assert policy.sleep_interval_mins == DEFAULT_SLEEP_INTERVAL_MINUTES
    assert policy.hourly_cap == DEFAULT_HOURLY_CAP
    assert policy.cooldown_days == DEFAULT_COOLDOWN_DAYS
    assert policy.post_release_grace_hrs == DEFAULT_POST_RELEASE_GRACE_HOURS
    assert policy.queue_limit == DEFAULT_QUEUE_LIMIT
    assert policy.sonarr_search_mode == SonarrSearchMode(DEFAULT_SONARR_SEARCH_MODE)
    assert policy.lidarr_search_mode == LidarrSearchMode(DEFAULT_LIDARR_SEARCH_MODE)
    assert policy.readarr_search_mode == ReadarrSearchMode(DEFAULT_READARR_SEARCH_MODE)
    assert policy.whisparr_v2_search_mode == WhisparrV2SearchMode(DEFAULT_WHISPARR_V2_SEARCH_MODE)


# CutoffPolicy.


def test_cutoff_policy_fields_in_declaration_order() -> None:
    """CutoffPolicy exposes the four cutoff tunables in order."""
    assert _field_names(CutoffPolicy) == [
        "cutoff_enabled",
        "cutoff_batch_size",
        "cutoff_cooldown_days",
        "cutoff_hourly_cap",
    ]


def test_cutoff_policy_defaults_match_config() -> None:
    """CutoffPolicy defaults to disabled with the slower cutoff cadence."""
    policy = CutoffPolicy()
    assert policy.cutoff_enabled is False
    assert policy.cutoff_batch_size == DEFAULT_CUTOFF_BATCH_SIZE
    assert policy.cutoff_cooldown_days == DEFAULT_CUTOFF_COOLDOWN_DAYS
    assert policy.cutoff_hourly_cap == DEFAULT_CUTOFF_HOURLY_CAP


# UpgradePolicy.


def test_upgrade_policy_fields_in_declaration_order() -> None:
    """UpgradePolicy exposes the upgrade tunables + pool offsets + window size."""
    assert _field_names(UpgradePolicy) == [
        "upgrade_enabled",
        "upgrade_batch_size",
        "upgrade_cooldown_days",
        "upgrade_hourly_cap",
        "upgrade_sonarr_search_mode",
        "upgrade_lidarr_search_mode",
        "upgrade_readarr_search_mode",
        "upgrade_whisparr_v2_search_mode",
        "upgrade_item_offset",
        "upgrade_series_offset",
        "upgrade_series_window_size",
    ]


def test_upgrade_policy_defaults_match_config() -> None:
    """UpgradePolicy defaults to disabled with zero pool offsets."""
    policy = UpgradePolicy()
    assert policy.upgrade_enabled is False
    assert policy.upgrade_batch_size == DEFAULT_UPGRADE_BATCH_SIZE
    assert policy.upgrade_cooldown_days == DEFAULT_UPGRADE_COOLDOWN_DAYS
    assert policy.upgrade_hourly_cap == DEFAULT_UPGRADE_HOURLY_CAP
    assert policy.upgrade_sonarr_search_mode == SonarrSearchMode(DEFAULT_UPGRADE_SONARR_SEARCH_MODE)
    assert policy.upgrade_lidarr_search_mode == LidarrSearchMode(DEFAULT_UPGRADE_LIDARR_SEARCH_MODE)
    assert policy.upgrade_readarr_search_mode == ReadarrSearchMode(
        DEFAULT_UPGRADE_READARR_SEARCH_MODE
    )
    assert policy.upgrade_whisparr_v2_search_mode == WhisparrV2SearchMode(
        DEFAULT_UPGRADE_WHISPARR_V2_SEARCH_MODE
    )
    assert policy.upgrade_item_offset == 0
    assert policy.upgrade_series_offset == 0


# SchedulePolicy.


def test_schedule_policy_fields_in_declaration_order() -> None:
    """SchedulePolicy carries time-window, order, and page-offset state."""
    assert _field_names(SchedulePolicy) == [
        "allowed_time_window",
        "search_order",
        "missing_page_offset",
        "cutoff_page_offset",
    ]


def test_schedule_policy_defaults_match_config() -> None:
    """SchedulePolicy defaults to 24/7, fresh-install order, page 1."""
    policy = SchedulePolicy()
    assert policy.allowed_time_window == DEFAULT_ALLOWED_TIME_WINDOW
    assert policy.search_order == SearchOrder(DEFAULT_SEARCH_ORDER)
    assert policy.missing_page_offset == 1
    assert policy.cutoff_page_offset == 1


# RuntimeSnapshot.


def test_runtime_snapshot_fields_in_declaration_order() -> None:
    """RuntimeSnapshot carries the three dashboard telemetry columns."""
    assert _field_names(RuntimeSnapshot) == [
        "monitored_total",
        "unreleased_count",
        "snapshot_refreshed_at",
    ]


def test_runtime_snapshot_defaults_are_empty() -> None:
    """Fresh RuntimeSnapshot reports zero counts and no refresh yet."""
    snapshot = RuntimeSnapshot()
    assert snapshot.monitored_total == 0
    assert snapshot.unreleased_count == 0
    assert snapshot.snapshot_refreshed_at == ""


# InstanceTimestamps.


def test_instance_timestamps_fields_in_declaration_order() -> None:
    """InstanceTimestamps exposes exactly created_at and updated_at."""
    assert _field_names(InstanceTimestamps) == ["created_at", "updated_at"]


def test_instance_timestamps_requires_both_fields() -> None:
    """Both timestamp fields are required: no default silently hides bugs."""
    assert _field_defaults(InstanceTimestamps) == {}


# Cross-substruct invariants.


def test_substruct_field_sets_are_disjoint() -> None:
    """No field name appears in two different sub-structs.

    The facade migration in D.14 depends on this: a shadowed name
    would break ``@property`` delegation because flat access would
    become ambiguous.
    """
    seen: dict[str, type] = {}
    for cls in SUBSTRUCTS:
        for name in _field_names(cls):
            if name in seen:
                pytest.fail(
                    f"Field {name!r} appears on both {seen[name].__name__}"
                    f" and {cls.__name__}; sub-structs must partition Instance."
                )
            seen[name] = cls


def test_substruct_field_union_matches_flat_accessor_surface() -> None:
    """The union of sub-struct fields is exactly the flat accessors.

    After D.14 Instance holds the seven sub-structs as its own fields
    and exposes every flat name via ``@property`` delegation.  This
    test locks the invariant that the flat accessor surface is the
    disjoint union of the sub-struct fields, which is what allows the
    caller-migration batches (D.15 - D.19) to translate a flat access
    to exactly one sub-struct access without ambiguity.
    """
    substruct_fields: set[str] = set()
    for cls in SUBSTRUCTS:
        substruct_fields.update(_field_names(cls))
    assert substruct_fields == set(FLAT_TO_SUB.keys())


def test_flat_accessor_surface_covers_pre_refactor_instance_fields() -> None:
    """Every pre-refactor flat field is still reachable as a property.

    Locks that the D.14 facade did not drop any attribute: a caller
    that reads ``instance.<name>`` for any ``<name>`` in the 39-field
    pre-refactor surface still finds that attribute after D.14.  The
    pre-refactor field list is encoded directly in :data:`FLAT_TO_SUB`
    so the test stays meaningful even once the Instance dataclass no
    longer declares those fields itself.
    """
    instance = _minimal_instance()
    for flat_name in FLAT_TO_SUB:
        assert hasattr(instance, flat_name), (
            f"Instance no longer exposes the flat attribute {flat_name!r};"
            " the facade migration lost coverage."
        )


# Instance facade contract (D.14).


# Map every pre-refactor flat field to the sub-struct that owns it.
# Source of truth for the caller migrations in D.15 - D.19; a caller
# rewriting ``instance.batch_size`` to ``instance.missing.batch_size``
# keys off this table.  The table also drives the parametrised
# facade tests below; a missing entry here would silently skip
# coverage for that attribute.
FLAT_TO_SUB: dict[str, str] = {
    # InstanceCore
    "id": "core",
    "name": "core",
    "type": "core",
    "url": "core",
    "api_key": "core",
    "enabled": "core",
    # MissingPolicy
    "batch_size": "missing",
    "sleep_interval_mins": "missing",
    "hourly_cap": "missing",
    "cooldown_days": "missing",
    "post_release_grace_hrs": "missing",
    "queue_limit": "missing",
    "sonarr_search_mode": "missing",
    "lidarr_search_mode": "missing",
    "readarr_search_mode": "missing",
    "whisparr_v2_search_mode": "missing",
    # CutoffPolicy
    "cutoff_enabled": "cutoff",
    "cutoff_batch_size": "cutoff",
    "cutoff_cooldown_days": "cutoff",
    "cutoff_hourly_cap": "cutoff",
    # UpgradePolicy
    "upgrade_enabled": "upgrade",
    "upgrade_batch_size": "upgrade",
    "upgrade_cooldown_days": "upgrade",
    "upgrade_hourly_cap": "upgrade",
    "upgrade_sonarr_search_mode": "upgrade",
    "upgrade_lidarr_search_mode": "upgrade",
    "upgrade_readarr_search_mode": "upgrade",
    "upgrade_whisparr_v2_search_mode": "upgrade",
    "upgrade_item_offset": "upgrade",
    "upgrade_series_offset": "upgrade",
    "upgrade_series_window_size": "upgrade",
    # SchedulePolicy
    "allowed_time_window": "schedule",
    "search_order": "schedule",
    "missing_page_offset": "schedule",
    "cutoff_page_offset": "schedule",
    # RuntimeSnapshot
    "monitored_total": "snapshot",
    "unreleased_count": "snapshot",
    "snapshot_refreshed_at": "snapshot",
    # InstanceTimestamps
    "created_at": "timestamps",
    "updated_at": "timestamps",
}


# Writable replacement values for every flat field.  The value a setter
# test drives through Instance.<field> = X must differ from the
# pre-minimal-instance default so the test observes an actual change.
FLAT_WRITE_VALUES: dict[str, Any] = {
    # InstanceCore
    "id": 999,
    "name": "renamed",
    "type": InstanceType.radarr,
    "url": "http://rewritten:7878",
    "api_key": "rotated-key",
    "enabled": False,
    # MissingPolicy
    "batch_size": 7,
    "sleep_interval_mins": 45,
    "hourly_cap": 11,
    "cooldown_days": 33,
    "post_release_grace_hrs": 12,
    "queue_limit": 5,
    "sonarr_search_mode": SonarrSearchMode.season_context,
    "lidarr_search_mode": LidarrSearchMode.artist_context,
    "readarr_search_mode": ReadarrSearchMode.author_context,
    "whisparr_v2_search_mode": WhisparrV2SearchMode.season_context,
    # CutoffPolicy
    "cutoff_enabled": True,
    "cutoff_batch_size": 3,
    "cutoff_cooldown_days": 60,
    "cutoff_hourly_cap": 4,
    # UpgradePolicy
    "upgrade_enabled": True,
    "upgrade_batch_size": 9,
    "upgrade_cooldown_days": 120,
    "upgrade_hourly_cap": 2,
    "upgrade_sonarr_search_mode": SonarrSearchMode.season_context,
    "upgrade_lidarr_search_mode": LidarrSearchMode.artist_context,
    "upgrade_readarr_search_mode": ReadarrSearchMode.author_context,
    "upgrade_whisparr_v2_search_mode": WhisparrV2SearchMode.season_context,
    "upgrade_item_offset": 42,
    "upgrade_series_offset": 17,
    "upgrade_series_window_size": 12,
    # SchedulePolicy
    "allowed_time_window": "08:00-20:00",
    "search_order": SearchOrder.random,
    "missing_page_offset": 5,
    "cutoff_page_offset": 9,
    # RuntimeSnapshot
    "monitored_total": 1234,
    "unreleased_count": 56,
    "snapshot_refreshed_at": "2026-05-01T12:00:00.000Z",
    # InstanceTimestamps
    "created_at": "2099-01-01T00:00:00.000Z",
    "updated_at": "2099-02-02T00:00:00.000Z",
}


def _minimal_instance() -> Instance:
    """Build an Instance with only the 18 pre-refactor required kwargs.

    Every defaulted kwarg falls back to the pre-D.14 Instance default
    value, so the result doubles as a default-preservation probe:
    tests that mutate a single field and then read it back can trust
    that untouched fields stay at their documented defaults.
    """
    return Instance(
        id=1,
        name="Test",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="plaintext-key",
        enabled=True,
        batch_size=2,
        sleep_interval_mins=30,
        hourly_cap=4,
        cooldown_days=14,
        post_release_grace_hrs=6,
        queue_limit=0,
        cutoff_enabled=False,
        cutoff_batch_size=1,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-02T00:00:00Z",
    )


def test_instance_is_dataclass_with_seven_sub_struct_fields() -> None:
    """Instance is a dataclass whose fields are the seven sub-structs.

    ``@dataclass(init=False)`` keeps auto-generated ``__eq__`` /
    ``__repr__`` across the seven sub-structs while the custom
    ``__init__`` accepts the 39 flat kwargs.
    """
    assert dataclasses.is_dataclass(Instance)
    expected = [
        ("core", InstanceCore),
        ("missing", MissingPolicy),
        ("cutoff", CutoffPolicy),
        ("upgrade", UpgradePolicy),
        ("schedule", SchedulePolicy),
        ("snapshot", RuntimeSnapshot),
        ("timestamps", InstanceTimestamps),
    ]
    observed = [(f.name, f.type) for f in dataclasses.fields(Instance)]
    # Dataclass field types arrive as string annotations under PEP 563 /
    # ``from __future__ import annotations``, so compare by name.
    assert [name for name, _ in observed] == [name for name, _ in expected]
    for (_, typ), (_, expected_typ) in zip(observed, expected, strict=True):
        # ``f.type`` is the stringified annotation ("InstanceCore").
        assert typ == expected_typ.__name__


def test_instance_is_not_frozen() -> None:
    """Instance stays mutable so the slots audit exception still holds."""
    params = Instance.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen is False


def test_instance_accepts_pre_refactor_flat_kwargs() -> None:
    """Calling ``Instance`` with the 18 required flat kwargs succeeds.

    Backwards-compat check for the 14+ caller sites that still build
    Instance via flat kwargs through D.15 - D.19.  Drop this test only
    after D.20 migrates every construction site to sub-struct form.
    """
    instance = _minimal_instance()
    assert isinstance(instance, Instance)
    assert instance.id == 1
    assert instance.name == "Test"
    assert instance.type == InstanceType.sonarr
    assert instance.url == "http://sonarr:8989"
    assert instance.api_key == "plaintext-key"
    assert instance.enabled is True


def test_instance_sub_struct_fields_populated_by_flat_init() -> None:
    """Flat __init__ kwargs land in the matching sub-struct field."""
    instance = _minimal_instance()
    assert isinstance(instance.core, InstanceCore)
    assert isinstance(instance.missing, MissingPolicy)
    assert isinstance(instance.cutoff, CutoffPolicy)
    assert isinstance(instance.upgrade, UpgradePolicy)
    assert isinstance(instance.schedule, SchedulePolicy)
    assert isinstance(instance.snapshot, RuntimeSnapshot)
    assert isinstance(instance.timestamps, InstanceTimestamps)
    assert instance.core.id == 1
    assert instance.missing.batch_size == 2
    assert instance.cutoff.cutoff_batch_size == 1
    assert instance.upgrade.upgrade_enabled is False
    assert instance.schedule.missing_page_offset == 1
    assert instance.snapshot.monitored_total == 0
    assert instance.timestamps.created_at == "2024-01-01T00:00:00Z"


def test_instance_flat_kwarg_defaults_preserve_pre_refactor_values() -> None:
    """The 21 defaulted flat kwargs resolve to the pre-D.14 defaults.

    Pre-D.14 Instance defaulted ``search_order`` to
    :attr:`SearchOrder.chronological` even though
    :data:`DEFAULT_SEARCH_ORDER` is ``"random"``.  The facade must
    preserve that exact contract so migrated rows that never wrote a
    search_order column keep iterating chronologically.  Same logic
    for every other defaulted kwarg: the Instance facade is the
    authority, not the sub-struct's direct default.
    """
    instance = _minimal_instance()
    assert instance.sonarr_search_mode == SonarrSearchMode.episode
    assert instance.lidarr_search_mode == LidarrSearchMode.album
    assert instance.readarr_search_mode == ReadarrSearchMode.book
    assert instance.whisparr_v2_search_mode == WhisparrV2SearchMode.episode
    assert instance.upgrade_enabled is False
    assert instance.upgrade_sonarr_search_mode == SonarrSearchMode.episode
    assert instance.upgrade_lidarr_search_mode == LidarrSearchMode.album
    assert instance.upgrade_readarr_search_mode == ReadarrSearchMode.book
    assert instance.upgrade_whisparr_v2_search_mode == WhisparrV2SearchMode.episode
    assert instance.upgrade_item_offset == 0
    assert instance.upgrade_series_offset == 0
    assert instance.missing_page_offset == 1
    assert instance.cutoff_page_offset == 1
    assert instance.allowed_time_window == ""
    assert instance.search_order == SearchOrder.chronological
    assert instance.monitored_total == 0
    assert instance.unreleased_count == 0
    assert instance.snapshot_refreshed_at == ""


@pytest.mark.parametrize("flat_name", sorted(FLAT_TO_SUB))
def test_flat_read_equals_sub_struct_read(flat_name: str) -> None:
    """Every flat attribute reads through to the matching sub-struct field."""
    instance = _minimal_instance()
    sub_name = FLAT_TO_SUB[flat_name]
    sub_value = getattr(getattr(instance, sub_name), flat_name)
    assert getattr(instance, flat_name) == sub_value


@pytest.mark.parametrize("flat_name", sorted(FLAT_TO_SUB))
def test_flat_write_propagates_to_sub_struct(flat_name: str) -> None:
    """Writing a flat attribute rebuilds the owning sub-struct.

    Locks the ``@property`` setter contract: after
    ``instance.<flat> = X``, both ``instance.<flat>`` and
    ``instance.<sub>.<flat>`` return ``X``.  Since sub-structs are
    frozen, the setter has to route through
    :func:`dataclasses.replace`; a regression here would surface as
    a :class:`dataclasses.FrozenInstanceError` from the setter.
    """
    instance = _minimal_instance()
    sub_name = FLAT_TO_SUB[flat_name]
    new_value = FLAT_WRITE_VALUES[flat_name]
    setattr(instance, flat_name, new_value)
    assert getattr(instance, flat_name) == new_value
    assert getattr(getattr(instance, sub_name), flat_name) == new_value


def test_sub_struct_swap_visible_via_flat_read() -> None:
    """Reassigning a whole sub-struct propagates to flat reads.

    Exercises the "sub-struct write" side of the D.14 both-patterns
    contract: a caller that has migrated to sub-struct construction
    (``instance.missing = MissingPolicy(batch_size=99, ...)``) still
    stays consistent with flat readers that have not migrated yet.
    """
    instance = _minimal_instance()
    assert instance.batch_size == 2  # baseline from _minimal_instance
    instance.missing = MissingPolicy(batch_size=99)
    assert instance.batch_size == 99
    assert instance.missing.batch_size == 99


def test_sub_struct_swap_isolated_to_its_group() -> None:
    """Swapping one sub-struct does not disturb the others.

    A sub-struct assignment must not implicitly reset peer sub-structs;
    the facade stores them as seven independent fields.
    """
    instance = _minimal_instance()
    original_core = instance.core
    original_schedule = instance.schedule
    instance.missing = MissingPolicy(batch_size=50)
    assert instance.core is original_core
    assert instance.schedule is original_schedule


def test_pre_refactor_instance_field_count_via_flat_accessors() -> None:
    """The D.14 facade exposes exactly 40 flat accessors after PR22.

    Canary that the facade migration did not quietly grow or shrink
    the flat surface.  :data:`FLAT_TO_SUB` is the authoritative
    encoding of that surface; bumping it requires a deliberate update
    with a matching accessor pair on :class:`Instance`.  PR22 added
    ``upgrade_series_window_size`` for per-instance Sonarr/Whisparr-v2
    upgrade-pool window tuning, taking the count from 39 to 40.
    """
    assert len(FLAT_TO_SUB) == 40
    instance = _minimal_instance()
    for flat_name in FLAT_TO_SUB:
        assert hasattr(instance, flat_name)

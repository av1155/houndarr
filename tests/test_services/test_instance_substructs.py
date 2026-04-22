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
    DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE,
    DEFAULT_WHISPARR_SEARCH_MODE,
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
    WhisparrSearchMode,
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
        "whisparr_search_mode",
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
    assert policy.whisparr_search_mode == WhisparrSearchMode(DEFAULT_WHISPARR_SEARCH_MODE)


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
    """UpgradePolicy exposes the ten upgrade tunables + pool offsets."""
    assert _field_names(UpgradePolicy) == [
        "upgrade_enabled",
        "upgrade_batch_size",
        "upgrade_cooldown_days",
        "upgrade_hourly_cap",
        "upgrade_sonarr_search_mode",
        "upgrade_lidarr_search_mode",
        "upgrade_readarr_search_mode",
        "upgrade_whisparr_search_mode",
        "upgrade_item_offset",
        "upgrade_series_offset",
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
    assert policy.upgrade_whisparr_search_mode == WhisparrSearchMode(
        DEFAULT_UPGRADE_WHISPARR_SEARCH_MODE
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


def test_substruct_field_union_equals_instance_fields() -> None:
    """The union of the seven sub-struct fields equals Instance's fields.

    Locks the exhaustive decomposition so D.14 can wrap Instance as a
    facade over sub-structs without orphaning a column or introducing
    a new one.
    """
    substruct_fields: set[str] = set()
    for cls in SUBSTRUCTS:
        substruct_fields.update(_field_names(cls))
    instance_fields = {f.name for f in dataclasses.fields(Instance)}
    assert substruct_fields == instance_fields


def test_substruct_field_count_matches_instance() -> None:
    """The seven sub-structs total exactly as many fields as Instance.

    Redundant with the union check but a clearer failure mode when
    someone adds a field to a sub-struct without removing it from
    Instance (or vice versa).
    """
    total = sum(len(_field_names(cls)) for cls in SUBSTRUCTS)
    assert total == len(dataclasses.fields(Instance))

"""Consolidated invariant: the Instance sub-struct layout stays whole.

Per-module pinning tests cover each service and repository
individually; this gate locks the structural shape above them.

Locked invariants:

* :class:`~houndarr.services.instances.Instance` is a plain dataclass
  with exactly seven sub-struct fields (``core``, ``missing``,
  ``cutoff``, ``upgrade``, ``schedule``, ``snapshot``,
  ``timestamps``); no flat-attribute facade wraps them.
* Every retired flat attribute name raises ``AttributeError`` when
  accessed on an Instance; callers must reach through the sub-struct.
* The four policy service modules (``metrics``, ``log_query``,
  ``instance_submit``, ``instance_validation``) expose their public
  API surfaces.
* The four repository modules (``settings``, ``instances``,
  ``cooldowns``, ``search_log``) expose their public API surfaces.
* :mod:`houndarr.deps` exports both :func:`get_supervisor` and
  :func:`get_master_key`.
* The 40-name flat attribute surface an Instance used to expose is
  covered exhaustively by the seven sub-structs' field sets; no
  historical column name is orphaned.
"""

from __future__ import annotations

import dataclasses

import pytest

from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    MissingPolicy,
    RuntimeSnapshot,
    SchedulePolicy,
    UpgradePolicy,
)

pytestmark = pytest.mark.pinning


# Instance shape


def test_instance_is_plain_dataclass_with_seven_sub_struct_fields() -> None:
    """Instance's declared dataclass fields are the seven sub-structs.

    Instance is now ``@dataclass(frozen=True, slots=True)`` and its
    field surface is the seven sub-structs listed below; any
    future field add or rename surfaces here.
    """
    assert dataclasses.is_dataclass(Instance)
    params = Instance.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen is True

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
    assert [n for n, _ in observed] == [n for n, _ in expected]
    for (_, typ), (_, expected_typ) in zip(observed, expected, strict=True):
        # ``from __future__ import annotations`` stringifies field types.
        assert typ == expected_typ.__name__


def test_instance_rejects_pre_refactor_flat_kwargs() -> None:
    """The flat-kwarg __init__ surface raises ``TypeError``."""
    with pytest.raises(TypeError):
        Instance(  # type: ignore[call-arg]
            id=1,
            name="legacy",
            type=InstanceType.sonarr,
            url="http://host:8989",
            api_key="k",
            enabled=True,
        )


def test_flat_attribute_access_raises_attribute_error() -> None:
    """Reading Instance.batch_size on a facade-free Instance fails loudly."""
    inst = _make_instance()
    with pytest.raises(AttributeError):
        _ = inst.batch_size  # type: ignore[attr-defined]


def test_flat_attribute_write_raises() -> None:
    """Writing to any name on a frozen Instance fails.

    Instance is ``@dataclass(frozen=True, slots=True)``.  Writes to
    a declared sub-struct raise ``FrozenInstanceError``; writes to
    a non-slot name hit a different rejection path
    (CPython issue: frozen + slots synthesizes a ``__setattr__`` whose
    ``super().__setattr__`` fall-through raises ``TypeError``).  The
    test accepts every rejection shape so a future CPython fix does
    not cause a spurious failure; what matters is that no write path
    silently succeeds.
    """
    import dataclasses

    inst = _make_instance()
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        inst.batch_size = 5  # type: ignore[attr-defined,misc]


def _make_instance() -> Instance:
    """Return a default Instance for the shape tests."""
    return Instance(
        core=InstanceCore(
            id=1,
            name="t",
            type=InstanceType.sonarr,
            url="http://host:8989",
            api_key="k",
        ),
        missing=MissingPolicy(),
        cutoff=CutoffPolicy(),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-02T00:00:00Z",
        ),
    )


# Service-layer public APIs


def test_metrics_service_public_api() -> None:
    """services.metrics exposes the D-batch surface."""
    from houndarr.services import metrics

    expected = {
        "EMPTY_METRICS",
        "gather_window_metrics",
        "gather_lifetime_metrics",
        "gather_active_errors",
        "gather_recent_searches",
        "gather_cooldown_data",
        "gather_dashboard_status",
    }
    for name in expected:
        assert hasattr(metrics, name), f"missing services.metrics.{name}"


def test_log_query_service_public_api() -> None:
    """services.log_query exposes the D-batch surface."""
    from houndarr.services import log_query

    expected = {
        "LIMIT_DEFAULT",
        "LIMIT_MAX",
        "query_logs",
        "summarize_rows",
        "compute_load_more_limit",
    }
    for name in expected:
        assert hasattr(log_query, name), f"missing services.log_query.{name}"


def test_instance_submit_service_public_api() -> None:
    """services.instance_submit exposes the D-batch surface."""
    from houndarr.services import instance_submit

    expected = {
        "InstanceNotFoundError",
        "submit_create",
        "submit_update",
    }
    for name in expected:
        assert hasattr(instance_submit, name), f"missing services.instance_submit.{name}"


def test_instance_validation_service_public_api() -> None:
    """services.instance_validation exposes the D-batch surface."""
    from houndarr.services import instance_validation

    expected = {
        "API_KEY_UNCHANGED",
        "ConnectionCheck",
        "ConnectionTestOutcome",
        "SearchModes",
        "build_client",
        "check_connection",
        "resolve_search_modes",
        "run_connection_test",
        "type_mismatch_message",
        "validate_cutoff_controls",
        "validate_upgrade_controls",
    }
    for name in expected:
        assert hasattr(instance_validation, name), f"missing services.instance_validation.{name}"


# Repository-layer public APIs


def test_settings_repository_public_api() -> None:
    """repositories.settings exposes the D-batch surface."""
    from houndarr.repositories import settings

    expected = {"get_setting", "set_setting", "delete_setting"}
    for name in expected:
        assert hasattr(settings, name), f"missing repositories.settings.{name}"


def test_instances_repository_public_api() -> None:
    """repositories.instances exposes the D-batch surface."""
    from houndarr.repositories import instances

    expected = {
        "InstanceInsert",
        "InstanceUpdate",
        "list_instances",
        "get_instance",
        "insert_instance",
        "update_instance",
        "delete_instance",
        "update_instance_snapshot",
    }
    for name in expected:
        assert hasattr(instances, name), f"missing repositories.instances.{name}"


def test_cooldowns_repository_public_api() -> None:
    """repositories.cooldowns exposes the D-batch surface."""
    from houndarr.repositories import cooldowns

    expected = {
        "exists_active_cooldown",
        "upsert_cooldown",
        "delete_cooldowns_for_instance",
    }
    for name in expected:
        assert hasattr(cooldowns, name), f"missing repositories.cooldowns.{name}"


def test_search_log_repository_public_api() -> None:
    """repositories.search_log exposes the full search-log write + fetch surface."""
    from houndarr.repositories import search_log

    expected = {
        "insert_log_row",
        "fetch_log_rows",
        "fetch_recent_searches",
        "delete_logs_for_instance",
        "delete_all_logs",
        "insert_admin_audit",
        "fetch_latest_missing_reason",
        "fetch_active_error_instance_ids",
    }
    for name in expected:
        assert hasattr(search_log, name), f"missing repositories.search_log.{name}"


# Depends shim surface


def test_deps_module_exports_both_shims() -> None:
    """houndarr.deps exports both ``get_supervisor`` and ``get_master_key``."""
    from houndarr import deps

    assert hasattr(deps, "get_supervisor")
    assert hasattr(deps, "get_master_key")
    assert callable(deps.get_supervisor)
    assert callable(deps.get_master_key)


# Sub-struct coverage of the historical flat surface


_PRE_REFACTOR_FLAT_FIELDS = {
    # InstanceCore
    "id",
    "name",
    "type",
    "url",
    "api_key",
    "enabled",
    # MissingPolicy
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
    # CutoffPolicy
    "cutoff_enabled",
    "cutoff_batch_size",
    "cutoff_cooldown_days",
    "cutoff_hourly_cap",
    # UpgradePolicy
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
    # SchedulePolicy
    "allowed_time_window",
    "search_order",
    "missing_page_offset",
    "cutoff_page_offset",
    # RuntimeSnapshot
    "monitored_total",
    "unreleased_count",
    "snapshot_refreshed_at",
    # InstanceTimestamps
    "created_at",
    "updated_at",
}


def test_sub_struct_field_union_covers_pre_refactor_surface() -> None:
    """The seven sub-structs together own every historical flat column."""
    substructs = (
        InstanceCore,
        MissingPolicy,
        CutoffPolicy,
        UpgradePolicy,
        SchedulePolicy,
        RuntimeSnapshot,
        InstanceTimestamps,
    )
    union: set[str] = set()
    for cls in substructs:
        union.update(f.name for f in dataclasses.fields(cls))
    assert union == _PRE_REFACTOR_FLAT_FIELDS
    assert len(union) == 40

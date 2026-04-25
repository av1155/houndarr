"""Pinning tests for the search_log-repository SQL boundary.

Locks the contract of ``insert_log_row``, ``fetch_log_rows``,
``fetch_recent_searches``, ``delete_logs_for_instance``, and
``purge_old_logs``.  The golden-log characterisation test in
``tests/test_engine/test_golden_search_log.py`` pins the engine's
``_write_log`` byte shape; these tests pin the repository
primitives the delegator rests on plus the fetch surface that
:mod:`houndarr.services.log_query` composes.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.repositories import search_log as repo


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Two stub instance rows so FK constraints are satisfied."""
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
                (2, "Radarr Test", "radarr", "http://radarr:7878"),
            ],
        )
        await conn.commit()
    yield


async def _count_logs() -> int:
    async with get_db() as conn, conn.execute("SELECT COUNT(*) FROM search_log") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_log_row_full_row_round_trip(seeded_instances: None) -> None:
    """Every kwarg survives into a column that reads back identically."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="searched",
        search_kind="missing",
        cycle_id="c-1",
        cycle_trigger="scheduled",
        item_label="Example S01E01",
        reason=None,
        message=None,
    )

    rows = await repo.fetch_log_rows(instance_id=1)
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == 1
    assert row["item_id"] == 42
    assert row["item_type"] == "episode"
    assert row["action"] == "searched"
    assert row["search_kind"] == "missing"
    assert row["cycle_id"] == "c-1"
    assert row["cycle_trigger"] == "scheduled"
    assert row["item_label"] == "Example S01E01"
    assert row["reason"] is None
    assert row["message"] is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_log_row_accepts_null_instance(seeded_instances: None) -> None:
    """System-scope rows carry a null instance_id; the FK allows it."""
    await repo.insert_log_row(
        instance_id=None,
        item_id=None,
        item_type=None,
        action="info",
        message="app started",
    )
    rows = await repo.fetch_log_rows()
    assert len(rows) == 1
    assert rows[0]["instance_id"] is None
    assert rows[0]["action"] == "info"
    assert rows[0]["message"] == "app started"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_log_row_populates_timestamp_from_default(
    seeded_instances: None,
) -> None:
    """timestamp is not a parameter; the schema default fills it in."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=None,
        item_type=None,
        action="info",
        message="hello",
    )
    rows = await repo.fetch_log_rows(instance_id=1)
    assert rows[0]["timestamp"].endswith("Z")
    assert "T" in rows[0]["timestamp"]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_log_rows_returns_empty_list_on_empty_table(seeded_instances: None) -> None:
    """Empty table returns [], not None."""
    assert await repo.fetch_log_rows() == []


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_log_rows_orders_newest_first(seeded_instances: None) -> None:
    """Rows sort by timestamp DESC, id DESC so the newest row leads."""
    for idx in range(3):
        await repo.insert_log_row(
            instance_id=1,
            item_id=idx,
            item_type="episode",
            action="searched",
            search_kind="missing",
            cycle_id=f"cycle-{idx}",
        )

    rows = await repo.fetch_log_rows()
    assert [r["cycle_id"] for r in rows] == ["cycle-2", "cycle-1", "cycle-0"]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_log_rows_applies_instance_filter(seeded_instances: None) -> None:
    """instance_id filter limits results to the named instance."""
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=2, item_id=1, item_type="movie", action="searched", search_kind="missing"
    )

    rows_1 = await repo.fetch_log_rows(instance_id=1)
    rows_2 = await repo.fetch_log_rows(instance_id=2)
    assert [r["instance_id"] for r in rows_1] == [1]
    assert [r["instance_id"] for r in rows_2] == [2]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_log_rows_applies_action_and_kind_filters(
    seeded_instances: None,
) -> None:
    """Filters combine via AND; only matching rows survive."""
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=1, item_id=2, item_type="episode", action="skipped", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=1, item_id=3, item_type="episode", action="searched", search_kind="cutoff"
    )

    rows = await repo.fetch_log_rows(action="searched", search_kind="missing")
    assert len(rows) == 1
    assert rows[0]["item_id"] == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_log_rows_limit_and_cursor(seeded_instances: None) -> None:
    """limit clamps the page size; after_id advances to the next page."""
    ids = []
    for idx in range(5):
        await repo.insert_log_row(
            instance_id=1,
            item_id=idx,
            item_type="episode",
            action="searched",
            search_kind="missing",
        )
        rows = await repo.fetch_log_rows(limit=1)
        ids.append(rows[0]["id"])

    page = await repo.fetch_log_rows(limit=2)
    assert len(page) == 2
    # Newest first: the two highest ids
    assert page[0]["id"] == ids[-1]

    next_page = await repo.fetch_log_rows(limit=2, after_id=page[-1]["id"])
    assert len(next_page) == 2
    # After-id is strict (<), so the cursor row itself is excluded
    assert next_page[0]["id"] < page[-1]["id"]


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_recent_searches_counts_only_searched(seeded_instances: None) -> None:
    """fetch_recent_searches only counts action='searched', inside the window."""
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=1, item_id=2, item_type="episode", action="skipped", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=1, item_id=3, item_type="episode", action="error", search_kind="missing"
    )

    count = await repo.fetch_recent_searches(1, search_kind="missing", within_seconds=3600)
    assert count == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_recent_searches_applies_time_window(seeded_instances: None) -> None:
    """Rows outside the trailing window do not count."""
    # Fresh row (inside any positive window)
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    # Stale row (backdate by 10 hours)
    stale = (datetime.now(UTC) - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO search_log (instance_id, item_id, item_type, action, search_kind,"
            " timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (1, 2, "episode", "searched", "missing", stale),
        )
        await conn.commit()

    within_hour = await repo.fetch_recent_searches(1, search_kind="missing", within_seconds=3600)
    within_day = await repo.fetch_recent_searches(1, search_kind="missing", within_seconds=86400)
    assert within_hour == 1
    assert within_day == 2


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_recent_searches_short_circuits_on_non_positive_window(
    seeded_instances: None,
) -> None:
    """Zero / negative within_seconds returns 0 without querying."""
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    assert await repo.fetch_recent_searches(1, search_kind="missing", within_seconds=0) == 0
    assert await repo.fetch_recent_searches(1, search_kind="missing", within_seconds=-1) == 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_recent_searches_scopes_to_instance_and_kind(
    seeded_instances: None,
) -> None:
    """Only rows that match instance_id AND search_kind count."""
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=1, item_id=2, item_type="episode", action="searched", search_kind="cutoff"
    )
    await repo.insert_log_row(
        instance_id=2, item_id=3, item_type="movie", action="searched", search_kind="missing"
    )

    assert await repo.fetch_recent_searches(1, search_kind="missing", within_seconds=3600) == 1
    assert await repo.fetch_recent_searches(1, search_kind="cutoff", within_seconds=3600) == 1
    assert await repo.fetch_recent_searches(2, search_kind="missing", within_seconds=3600) == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_logs_for_instance_returns_row_count(seeded_instances: None) -> None:
    """delete_logs_for_instance returns the number of rows removed."""
    await repo.insert_log_row(
        instance_id=1, item_id=1, item_type="episode", action="searched", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=1, item_id=2, item_type="episode", action="skipped", search_kind="missing"
    )
    await repo.insert_log_row(
        instance_id=2, item_id=1, item_type="movie", action="searched", search_kind="missing"
    )

    deleted = await repo.delete_logs_for_instance(1)
    assert deleted == 2
    assert await _count_logs() == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_logs_for_instance_returns_zero_when_empty(
    seeded_instances: None,
) -> None:
    """delete_logs_for_instance returns 0 when there are no matching rows."""
    assert await repo.delete_logs_for_instance(1) == 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_returns_newest_reason(
    seeded_instances: None,
) -> None:
    """fetch_latest_missing_reason returns the newest missing-pass reason."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="not yet released",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (3h)",
    )
    assert await repo.fetch_latest_missing_reason(1, 42, "episode") == "post-release grace (3h)"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_returns_none_when_no_match(
    seeded_instances: None,
) -> None:
    """fetch_latest_missing_reason returns None when no missing-pass rows exist."""
    assert await repo.fetch_latest_missing_reason(1, 99, "episode") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_ignores_non_missing_rows(
    seeded_instances: None,
) -> None:
    """fetch_latest_missing_reason only consults missing-pass rows."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=5,
        item_type="episode",
        action="skipped",
        search_kind="cutoff",
        reason="cutoff-only reason",
    )
    assert await repo.fetch_latest_missing_reason(1, 5, "episode") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_active_error_instance_ids_includes_recent_error(
    seeded_instances: None,
) -> None:
    """fetch_active_error_instance_ids flags instances whose newest row errored."""
    await repo.insert_log_row(
        instance_id=1, item_id=None, item_type=None, action="error", message="boom"
    )
    assert await repo.fetch_active_error_instance_ids() == {1}


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_active_error_instance_ids_excludes_when_error_is_superseded(
    seeded_instances: None,
) -> None:
    """A non-error row newer than the error clears the flag."""
    await repo.insert_log_row(
        instance_id=1, item_id=None, item_type=None, action="error", message="boom"
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=1,
        item_type="episode",
        action="searched",
    )
    assert await repo.fetch_active_error_instance_ids() == set()


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_active_error_instance_ids_returns_empty_on_no_errors(
    seeded_instances: None,
) -> None:
    """fetch_active_error_instance_ids returns the empty set when nothing errored."""
    await repo.insert_log_row(instance_id=1, item_id=1, item_type="episode", action="searched")
    assert await repo.fetch_active_error_instance_ids() == set()


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_engine_count_searches_last_hour_delegates_through_repo(
    seeded_instances: None,
) -> None:
    """The engine's _count_searches_last_hour reads through fetch_recent_searches."""
    from houndarr.engine.search_loop import _count_searches_last_hour

    await repo.insert_log_row(
        instance_id=1,
        item_id=1,
        item_type="episode",
        action="searched",
        search_kind="missing",
    )
    assert await _count_searches_last_hour(1, "missing") == 1


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_engine_latest_missing_reason_ref_delegates_through_repo(
    seeded_instances: None,
) -> None:
    """The engine's _latest_missing_reason_ref reads through fetch_latest_missing_reason."""
    from houndarr.engine.search_loop import _latest_missing_reason_ref
    from houndarr.enums import ItemType
    from houndarr.value_objects import ItemRef

    await repo.insert_log_row(
        instance_id=1,
        item_id=7,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="not yet released",
    )
    ref = ItemRef(instance_id=1, item_id=7, item_type=ItemType.episode)
    assert await _latest_missing_reason_ref(ref) == "not yet released"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_services_active_error_instance_ids_delegates_through_repo(
    seeded_instances: None,
) -> None:
    """services.instances.active_error_instance_ids delegates to the repo."""
    from houndarr.services.instances import active_error_instance_ids

    await repo.insert_log_row(
        instance_id=2, item_id=None, item_type=None, action="error", message="broken"
    )
    assert await active_error_instance_ids() == {2}


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_all_logs_wipes_every_row(seeded_instances: None) -> None:
    """delete_all_logs returns the pre-wipe count and empties the table."""
    await repo.insert_log_row(instance_id=1, item_id=1, item_type="episode", action="searched")
    await repo.insert_log_row(instance_id=2, item_id=2, item_type="movie", action="skipped")
    await repo.insert_log_row(instance_id=None, item_id=None, item_type=None, action="info")

    removed = await repo.delete_all_logs()

    assert removed == 3
    assert await _count_logs() == 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_delete_all_logs_returns_zero_on_empty_table(seeded_instances: None) -> None:
    """delete_all_logs returns 0 when the table is already empty."""
    assert await repo.delete_all_logs() == 0


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_admin_audit_writes_system_info_row(seeded_instances: None) -> None:
    """insert_admin_audit writes a NULL-instance system/info breadcrumb."""
    await repo.insert_admin_audit("Audit log cleared by admin (5 rows removed)")

    rows = await repo.fetch_log_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] is None
    assert row["cycle_trigger"] == "system"
    assert row["action"] == "info"
    assert row["message"] == "Audit log cleared by admin (5 rows removed)"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_insert_admin_audit_appends_without_mutating_existing(
    seeded_instances: None,
) -> None:
    """insert_admin_audit is append-only; it does not disturb existing rows."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=101,
        item_type="episode",
        action="searched",
        item_label="Show S01E01",
    )
    await repo.insert_admin_audit("Policy reset by admin")

    rows = await repo.fetch_log_rows()
    assert len(rows) == 2
    # fetch_log_rows orders newest first; the audit row is newest.
    assert rows[0]["cycle_trigger"] == "system"
    assert rows[0]["action"] == "info"
    assert rows[1]["action"] == "searched"
    assert rows[1]["item_label"] == "Show S01E01"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_engine_write_log_delegates_through_repo(seeded_instances: None) -> None:
    """The engine's _write_log helper writes the same row shape the repo would."""
    from houndarr.engine.search_loop import _write_log

    await _write_log(
        1,
        42,
        "episode",
        "searched",
        search_kind="missing",
        cycle_id="c-eng",
        cycle_trigger="scheduled",
        item_label="Delegated Episode",
    )

    rows = await repo.fetch_log_rows(instance_id=1)
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == 1
    assert row["item_id"] == 42
    assert row["item_type"] == "episode"
    assert row["action"] == "searched"
    assert row["search_kind"] == "missing"
    assert row["cycle_id"] == "c-eng"
    assert row["cycle_trigger"] == "scheduled"
    assert row["item_label"] == "Delegated Episode"


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_purge_old_logs_lives_on_repository(db: None) -> None:
    """``purge_old_logs`` lives on the search-log repository.

    The function's disable-on-zero semantics and the empty-table
    return shape are pinned here; detailed row-deletion coverage
    stays in tests/test_database_edge_cases.py.  A companion
    assertion catches a future re-introduction of a shim on
    :mod:`houndarr.database`.
    """
    import houndarr.database as _database_mod
    from houndarr.repositories.search_log import purge_old_logs

    assert await purge_old_logs(0) == 0
    assert await purge_old_logs(-5) == 0
    assert await purge_old_logs(30) == 0
    assert not hasattr(_database_mod, "purge_old_logs")

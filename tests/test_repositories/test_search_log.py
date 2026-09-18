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
async def seeded_instances(db: None) -> AsyncGenerator[None]:
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


@pytest.mark.asyncio()
async def test_has_rows_for_cycle_true_when_row_exists(seeded_instances: None) -> None:
    """A row stamped with the cycle_id makes the probe return True."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=1,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        cycle_id="cycle-abc",
    )
    assert await repo.has_rows_for_cycle("cycle-abc") is True


@pytest.mark.asyncio()
async def test_has_rows_for_cycle_false_for_other_cycles(seeded_instances: None) -> None:
    """Rows from other cycles (or NULL cycle_id) do not match."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=1,
        item_type="episode",
        action="searched",
        search_kind="missing",
        cycle_id="cycle-abc",
    )
    await repo.insert_log_row(
        instance_id=1, item_id=2, item_type="episode", action="searched", search_kind="missing"
    )
    assert await repo.has_rows_for_cycle("cycle-other") is False


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
    """fetch_latest_missing_reason only consults missing-pass rows.

    The rows carry reasons and actions the missing pass does admit, so the
    pass scoping is the only thing that can exclude them. Season-context
    modes reuse one synthetic parent id across passes, so an upgrade
    dispatch must not read as a search of the missing item.
    """
    await repo.insert_log_row(
        instance_id=1,
        item_id=5,
        item_type="episode",
        action="searched",
        search_kind="upgrade",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=5,
        item_type="episode",
        action="skipped",
        search_kind="cutoff",
        reason="post-release grace (6h)",
    )
    assert await repo.fetch_latest_missing_reason(1, 5, "episode") is None


@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_ignores_queue_skips(
    seeded_instances: None,
) -> None:
    """A newer 'already in download queue' row does not hide the release-timing reason."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=7,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (6h)",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=7,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="already in download queue",
    )
    assert await repo.fetch_latest_missing_reason(1, 7, "episode") == "post-release grace (6h)"


@pytest.mark.parametrize(
    "later_reason",
    [
        "on cooldown (7d)",
        "hourly limit reached (20/hr)",
        "in hot retry window (24h)",
        "tag filter (excluded tag)",
        "already in download queue",
    ],
)
@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_ignores_other_gate_rows(
    seeded_instances: None,
    later_reason: str,
) -> None:
    """Skips written by gates other than release timing neither arm nor cancel a retry."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=11,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (6h)",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=11,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason=later_reason,
    )

    assert await repo.fetch_latest_missing_reason(1, 11, "episode") == "post-release grace (6h)"


@pytest.mark.parametrize(
    "later_reason",
    [
        "radarr reports not available",
        "radarr status indicates unreleased",
        "whisparr v3 reports not available",
        "whisparr v3 status indicates unreleased",
        "future title not yet available",
        "no series linked",
    ],
)
@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_keeps_availability_rows(
    seeded_instances: None,
    later_reason: str,
) -> None:
    """A per-app availability skip still supersedes an older grace row and cancels the retry."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=13,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (6h)",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=13,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason=later_reason,
    )

    assert await repo.fetch_latest_missing_reason(1, 13, "episode") == later_reason


@pytest.mark.parametrize("action", ["searched", "error"])
@pytest.mark.asyncio()
async def test_fetch_latest_missing_reason_returns_none_after_dispatch(
    seeded_instances: None,
    action: str,
) -> None:
    """A dispatch outcome after the release-timing skip still ends the retry."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=12,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="not yet released",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=12,
        item_type="episode",
        action=action,
        search_kind="missing",
        message="dispatch failed" if action == "error" else None,
    )

    assert await repo.fetch_latest_missing_reason(1, 12, "episode") is None


async def _seed_rows(rows: list[tuple[str, str | None, str]]) -> None:
    """Insert ``(action, reason, timestamp)`` rows for item 21 of instance 1."""
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, action, search_kind, reason, timestamp
            ) VALUES (1, 21, 'episode', ?, 'missing', ?, ?)
            """,
            rows,
        )
        await conn.commit()


@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_returns_newest_of_the_run(
    seeded_instances: None,
) -> None:
    """The newest grace skip written since the last dispatch bounds the wait."""
    await _seed_rows(
        [
            ("skipped", "post-release grace (6h)", "2026-05-22T08:00:00.000Z"),
            ("searched", None, "2026-05-22T09:00:00.000Z"),
            ("skipped", "post-release grace (6h)", "2026-05-22T10:00:00.000Z"),
            ("skipped", "post-release grace (6h)", "2026-05-22T11:00:00.000Z"),
        ]
    )

    result = await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode")

    assert result == "2026-05-22T11:00:00.000Z"


@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_without_any_dispatch(
    seeded_instances: None,
) -> None:
    """With no dispatch row at all, the newest grace skip counts."""
    await _seed_rows(
        [
            ("skipped", "post-release grace (6h)", "2026-05-22T08:00:00.000Z"),
            ("skipped", "not yet released", "2026-05-22T07:00:00.000Z"),
            ("skipped", "post-release grace (6h)", "2026-05-22T09:00:00.000Z"),
        ]
    )

    result = await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode")

    assert result == "2026-05-22T09:00:00.000Z"


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([], id="no-rows"),
        pytest.param(
            [("skipped", "post-release grace (6h)", "2026-05-22T08:00:00.000Z")],
            id="grace-before-dispatch",
        ),
        pytest.param(
            [("skipped", "not yet released", "2026-05-22T10:00:00.000Z")],
            id="unreleased-only",
        ),
    ],
)
@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_returns_none(
    seeded_instances: None,
    rows: list[tuple[str, str | None, str]],
) -> None:
    """No grace skip since the last dispatch means nothing bounds the wait."""
    await _seed_rows([*rows, ("searched", None, "2026-05-22T09:00:00.000Z")])

    assert await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode") is None


@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_breaks_timestamp_ties_by_id(
    seeded_instances: None,
) -> None:
    """A dispatch sharing the grace skip's timestamp still counts as newer."""
    await _seed_rows(
        [
            ("skipped", "post-release grace (6h)", "2026-05-22T09:00:00.000Z"),
            ("searched", None, "2026-05-22T09:00:00.000Z"),
        ]
    )

    assert await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode") is None


@pytest.mark.parametrize("action", ["searched", "error"])
@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_clears_on_either_dispatch(
    seeded_instances: None,
    action: str,
) -> None:
    """A failed search ends the wait the same way a successful one does."""
    await _seed_rows(
        [
            ("skipped", "post-release grace (6h)", "2026-05-22T08:00:00.000Z"),
            (action, None, "2026-05-22T09:00:00.000Z"),
        ]
    )

    assert await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode") is None


@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_keeps_grace_written_after_a_tie(
    seeded_instances: None,
) -> None:
    """A grace skip sharing the dispatch's timestamp but written after it still counts."""
    await _seed_rows(
        [
            ("searched", None, "2026-05-22T09:00:00.000Z"),
            ("skipped", "post-release grace (6h)", "2026-05-22T09:00:00.000Z"),
        ]
    )

    result = await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode")

    assert result == "2026-05-22T09:00:00.000Z"


@pytest.mark.parametrize(
    "grace_reason",
    ["post-release grace (1h)", "post-release grace (24h)", "post-release grace (168h)"],
)
@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_matches_any_window_label(
    seeded_instances: None,
    grace_reason: str,
) -> None:
    """The bound follows the reason prefix, not whichever window the default happens to be."""
    await _seed_rows([("skipped", grace_reason, "2026-05-22T08:00:00.000Z")])

    result = await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode")

    assert result == "2026-05-22T08:00:00.000Z"


@pytest.mark.parametrize(
    "later_reason",
    [
        "not yet released",
        "on cooldown (7d)",
        "already in download queue",
        "waiting on post-release grace (6h)",
    ],
)
@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_ignores_newer_skips(
    seeded_instances: None,
    later_reason: str,
) -> None:
    """Only a dispatch ends the wait; a newer skip of any other kind does not."""
    await _seed_rows(
        [
            ("skipped", "post-release grace (6h)", "2026-05-22T08:00:00.000Z"),
            ("skipped", later_reason, "2026-05-22T09:00:00.000Z"),
        ]
    )

    result = await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode")

    assert result == "2026-05-22T08:00:00.000Z"


@pytest.mark.parametrize(
    ("instance_id", "item_id", "item_type", "search_kind"),
    [
        pytest.param(2, 21, "episode", "missing", id="other-instance"),
        pytest.param(1, 99, "episode", "missing", id="other-item"),
        pytest.param(1, 21, "movie", "missing", id="other-item-type"),
        pytest.param(1, 21, "episode", "cutoff", id="cutoff-pass"),
        pytest.param(1, 21, "episode", "upgrade", id="upgrade-pass"),
    ],
)
@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_ignores_foreign_dispatches(
    seeded_instances: None,
    instance_id: int,
    item_id: int,
    item_type: str,
    search_kind: str,
) -> None:
    """A dispatch belonging to anything else must not clear this item's wait.

    Two instances holding the same series derive the same synthetic season id,
    and every season of one series shares an instance, so the dispatch side
    needs the same scoping the grace side has.
    """
    await _seed_rows([("skipped", "post-release grace (6h)", "2026-05-22T08:00:00.000Z")])
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, action, search_kind, reason, timestamp
            ) VALUES (?, ?, ?, 'searched', ?, NULL, '2026-05-22T09:00:00.000Z')
            """,
            (instance_id, item_id, item_type, search_kind),
        )
        await conn.commit()

    result = await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode")

    assert result == "2026-05-22T08:00:00.000Z"


@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_ignores_a_foreign_grace_row(
    seeded_instances: None,
) -> None:
    """Another season's grace row must not hold this one."""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, action, search_kind, reason, timestamp
            ) VALUES (1, 99, 'episode', 'skipped', 'missing',
                      'post-release grace (6h)', '2026-05-22T08:00:00.000Z')
            """
        )
        await conn.commit()

    assert await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode") is None


@pytest.mark.asyncio()
async def test_last_grace_skip_since_dispatch_scopes_by_ref_and_kind(
    seeded_instances: None,
) -> None:
    """Rows of another instance, item type, or pass do not bound this item."""
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, action, search_kind, reason, timestamp
            ) VALUES (?, 21, ?, 'skipped', ?, 'post-release grace (6h)', ?)
            """,
            [
                (2, "episode", "missing", "2026-05-22T08:00:00.000Z"),
                (1, "movie", "missing", "2026-05-22T08:00:00.000Z"),
                (1, "episode", "cutoff", "2026-05-22T08:00:00.000Z"),
            ],
        )
        await conn.commit()

    assert await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_grace_skip_returns_newest_match(
    seeded_instances: None,
) -> None:
    """fetch_latest_missing_grace_skip returns the newest grace skip tuple."""
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO search_log (
                instance_id, item_id, item_type, action, search_kind, reason, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    42,
                    "episode",
                    "skipped",
                    "missing",
                    "post-release grace (6h)",
                    "2026-05-22T10:00:00.000Z",
                ),
                (
                    1,
                    42,
                    "episode",
                    "searched",
                    "missing",
                    None,
                    "2026-05-22T11:00:00.000Z",
                ),
                (
                    1,
                    42,
                    "episode",
                    "skipped",
                    "missing",
                    "post-release grace (6h)",
                    "2026-05-22T12:00:00.000Z",
                ),
            ],
        )
        await conn.commit()

    result = await repo.fetch_latest_missing_grace_skip(1, 42, "episode")

    assert result == ("post-release grace (6h)", "2026-05-22T12:00:00.000Z")


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_grace_skip_returns_none_when_no_match(
    seeded_instances: None,
) -> None:
    """fetch_latest_missing_grace_skip returns None when no grace skip exists."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="not yet released",
    )

    assert await repo.fetch_latest_missing_grace_skip(1, 42, "episode") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_grace_skip_ignores_non_missing_rows(
    seeded_instances: None,
) -> None:
    """fetch_latest_missing_grace_skip only consults missing-pass rows."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="skipped",
        search_kind="cutoff",
        reason="post-release grace (6h)",
    )

    assert await repo.fetch_latest_missing_grace_skip(1, 42, "episode") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_grace_skip_scopes_by_ref_and_action(
    seeded_instances: None,
) -> None:
    """Grace anchors must match instance, item type, and skipped action."""
    await repo.insert_log_row(
        instance_id=2,
        item_id=42,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (6h)",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="movie",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (6h)",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="searched",
        search_kind="missing",
        reason="post-release grace (6h)",
    )

    assert await repo.fetch_latest_missing_grace_skip(1, 42, "episode") is None


@pytest.mark.pinning()
@pytest.mark.asyncio()
async def test_fetch_latest_missing_grace_skip_ignores_newer_searched_row(
    seeded_instances: None,
) -> None:
    """Newer searched rows do not hide the grace skip anchor."""
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="skipped",
        search_kind="missing",
        reason="post-release grace (6h)",
    )
    await repo.insert_log_row(
        instance_id=1,
        item_id=42,
        item_type="episode",
        action="searched",
        search_kind="missing",
        reason=None,
    )

    result = await repo.fetch_latest_missing_grace_skip(1, 42, "episode")

    assert result is not None
    reason, timestamp = result
    assert reason == "post-release grace (6h)"
    assert timestamp


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


@pytest.mark.asyncio()
async def test_the_group_hold_row_does_not_feed_the_wait_it_records(
    seeded_instances: None,
) -> None:
    """A parent's own hold row must stay outside both grace lookups.

    Each matches ``post-release grace%`` anchored.  If the hold reason
    were caught, every held cycle would push the wait's bound out and
    the parent would never take its retry (#783).
    """
    from houndarr.engine.search_loop import _format_group_hold_reason

    # Built the way the engine builds it, so renaming the reason into the
    # pattern's reach fails here rather than silently reviving the loop.
    hold = _format_group_hold_reason(6)
    await _seed_rows(
        [
            ("skipped", hold, "2026-05-22T09:00:00.000Z"),
            ("skipped", hold, "2026-05-22T10:00:00.000Z"),
        ]
    )

    assert await repo.fetch_last_missing_grace_skip_since_dispatch(1, 21, "episode") is None
    assert await repo.fetch_latest_missing_grace_skip(1, 21, "episode") is None
    assert await repo.fetch_latest_missing_reason(1, 21, "episode") is None

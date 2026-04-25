"""Tests for GET /api/logs/head (Logs page live-tail head-check)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from houndarr.database import get_db


async def _login(async_client: Any) -> None:
    """Create the admin and log in; required by AuthMiddleware for /api/logs*."""
    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


@pytest_asyncio.fixture()
async def seeded_cycles(db: None) -> AsyncGenerator[None, None]:
    """Seed search_log with three cycles across two instances.

    Cycle ordering by timestamp (newest first): cycle-c, cycle-b, cycle-a.
    cycle-a has two rows so the head snapshot exercises the "latest row
    of the cursor cycle" subquery path, not just first-row timestamps.
    """
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr", "sonarr", "http://sonarr:8989"),
                (2, "Radarr", "radarr", "http://radarr:7878"),
            ],
        )
        rows: list[tuple[Any, ...]] = [
            (
                1,
                101,
                "episode",
                "missing",
                "cycle-a",
                "scheduled",
                "Show A - S01E01",
                "searched",
                None,
                None,
                "2024-01-01T12:00:00.000Z",
            ),
            (
                1,
                102,
                "episode",
                "missing",
                "cycle-a",
                "scheduled",
                "Show A - S01E02",
                "skipped",
                "on cooldown",
                None,
                "2024-01-01T12:00:30.000Z",
            ),
            (
                2,
                201,
                "movie",
                "missing",
                "cycle-b",
                "run_now",
                "Movie B",
                "searched",
                None,
                None,
                "2024-01-01T12:05:00.000Z",
            ),
            (
                1,
                103,
                "episode",
                "missing",
                "cycle-c",
                "scheduled",
                "Show C - S01E01",
                "searched",
                None,
                None,
                "2024-01-01T12:10:00.000Z",
            ),
        ]
        await conn.executemany(
            """
            INSERT INTO search_log
                (
                    instance_id,
                    item_id,
                    item_type,
                    search_kind,
                    cycle_id,
                    cycle_trigger,
                    item_label,
                    action,
                    reason,
                    message,
                    timestamp
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()
    yield


@pytest.mark.asyncio()
async def test_head_returns_expected_keys(seeded_cycles: None, async_client: Any) -> None:
    """Response carries all four documented keys."""
    await _login(async_client)
    resp = await async_client.get("/api/logs/head")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"newest_cycle_id", "newest_timestamp", "count_newer_than", "at"}


@pytest.mark.asyncio()
async def test_head_newest_cycle_is_latest_by_timestamp(
    seeded_cycles: None, async_client: Any
) -> None:
    """``newest_cycle_id`` reflects the cycle whose latest row is most recent."""
    await _login(async_client)
    resp = await async_client.get("/api/logs/head")
    body = resp.json()
    assert body["newest_cycle_id"] == "cycle-c"
    assert body["newest_timestamp"] == "2024-01-01T12:10:00.000Z"


@pytest.mark.asyncio()
async def test_head_count_newer_than_cursor(seeded_cycles: None, async_client: Any) -> None:
    """``count_newer_than`` excludes the cursor and counts cycles with rows after it."""
    await _login(async_client)

    resp = await async_client.get("/api/logs/head?since_cycle_id=cycle-a")
    body = resp.json()
    # cycle-b and cycle-c are both newer than cycle-a's latest row
    assert body["count_newer_than"] == 2

    resp = await async_client.get("/api/logs/head?since_cycle_id=cycle-c")
    body = resp.json()
    # Nothing newer than the top cycle
    assert body["count_newer_than"] == 0


@pytest.mark.asyncio()
async def test_head_missing_cursor_returns_zero(seeded_cycles: None, async_client: Any) -> None:
    """A cursor that has been purged returns count=0 (client recovers on refresh)."""
    await _login(async_client)
    resp = await async_client.get("/api/logs/head?since_cycle_id=never-existed")
    body = resp.json()
    assert body["count_newer_than"] == 0


@pytest.mark.asyncio()
async def test_head_empty_db_returns_nulls(db: None, async_client: Any) -> None:
    """Empty search_log returns null identifiers and zero count."""
    await _login(async_client)
    resp = await async_client.get("/api/logs/head")
    assert resp.status_code == 200
    body = resp.json()
    assert body["newest_cycle_id"] is None
    assert body["newest_timestamp"] is None
    assert body["count_newer_than"] == 0

"""Pin the /api/status SQL rollups at the function boundary.

The seven SQL rollups live in :mod:`houndarr.services.metrics`;
these tests lock each helper's output shape and semantics via
seeded ``search_log`` rows.  Every test seeds its own rows to keep
scenarios independent; the existing
``tests/test_routes/test_status.py`` covers the full endpoint via
TestClient.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from houndarr.database import get_db
from houndarr.services.metrics import (
    gather_active_errors as _active_errors,
)
from houndarr.services.metrics import (
    gather_cooldown_data as _cooldown_data,
)
from houndarr.services.metrics import (
    gather_lifetime_metrics as _lifetime_metrics,
)
from houndarr.services.metrics import (
    gather_recent_searches as _recent_searches,
)
from houndarr.services.metrics import (
    gather_window_metrics as _all_instance_metrics,
)

pytestmark = pytest.mark.pinning


_ENC_KEY = (
    b"gAAAAABmX0000000000000000000000000000000000000000"
    b"000000000000000000000000000000000000000000000="
)


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed two instance rows with a valid Fernet-encrypted api_key sentinel."""
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO instances
            (id, name, type, url, encrypted_api_key, cooldown_days,
             cutoff_cooldown_days, upgrade_cooldown_days)
            VALUES (?, ?, ?, ?, ?, 14, 21, 90)
            """,
            [
                (1, "Sonarr", "sonarr", "http://sonarr:8989", _ENC_KEY),
                (2, "Radarr", "radarr", "http://radarr:7878", _ENC_KEY),
            ],
        )
        await conn.commit()
    yield


# _all_instance_metrics


class TestAllInstanceMetrics:
    @pytest.mark.asyncio()
    async def test_empty_instance_list_returns_empty(self, db: None) -> None:
        async with get_db() as conn:
            metrics, activity = await _all_instance_metrics(conn, [])
        assert metrics == {}
        assert activity == {}

    @pytest.mark.asyncio()
    async def test_counts_24h_actions(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.executemany(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp)
                VALUES (?, ?, 'movie', ?, datetime('now'))
                """,
                [
                    (1, 10, "searched"),
                    (1, 11, "searched"),
                    (1, 12, "skipped"),
                    (1, 13, "error"),
                    (1, 14, "info"),  # info rows are ignored by 24h counters
                ],
            )
            await conn.commit()
            metrics, _ = await _all_instance_metrics(conn, [1])
        assert metrics[1]["searched_24h"] == 2
        assert metrics[1]["skipped_24h"] == 1
        assert metrics[1]["errors_24h"] == 1

    @pytest.mark.asyncio()
    async def test_rows_older_than_24h_excluded(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp)
                VALUES (1, 10, 'movie', 'searched', datetime('now','-25 hours'))
                """,
            )
            await conn.commit()
            metrics, _ = await _all_instance_metrics(conn, [1])
        assert metrics.get(1, {"searched_24h": 0})["searched_24h"] == 0


# _lifetime_metrics


class TestLifetimeMetrics:
    @pytest.mark.asyncio()
    async def test_empty_instances_returns_empty(self, db: None) -> None:
        async with get_db() as conn:
            result = await _lifetime_metrics(conn, [])
        assert result == {}

    @pytest.mark.asyncio()
    async def test_counts_all_searched_rows_regardless_of_age(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.executemany(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp)
                VALUES (1, ?, 'movie', 'searched', ?)
                """,
                [
                    (10, "2024-01-01T00:00:00.000Z"),
                    (11, "2025-01-01T00:00:00.000Z"),
                    (12, "2026-01-01T00:00:00.000Z"),
                ],
            )
            await conn.commit()
            result = await _lifetime_metrics(conn, [1])
        assert result[1]["lifetime_searched"] == 3
        assert result[1]["last_dispatch_at"] is not None


# _active_errors


class TestActiveErrors:
    @pytest.mark.asyncio()
    async def test_empty_instances_returns_empty(self, db: None) -> None:
        async with get_db() as conn:
            result = await _active_errors(conn, [])
        assert result == {}

    @pytest.mark.asyncio()
    async def test_instance_without_error_row_excluded(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp)
                VALUES (1, 10, 'movie', 'searched', datetime('now'))
                """,
            )
            await conn.commit()
            result = await _active_errors(conn, [1, 2])
        assert 1 not in result
        assert 2 not in result

    @pytest.mark.asyncio()
    async def test_instance_with_error_as_latest_is_surfaced(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.executemany(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp, message)
                VALUES (1, ?, 'movie', ?, ?, ?)
                """,
                [
                    (10, "searched", "2026-04-25T10:00:00.000Z", None),
                    (11, "error", "2026-04-25T11:00:00.000Z", "connection refused"),
                ],
            )
            await conn.commit()
            result = await _active_errors(conn, [1])
        assert 1 in result
        assert "connection refused" in result[1]["message"]

    @pytest.mark.asyncio()
    async def test_error_cleared_by_later_non_error_row(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.executemany(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp, message)
                VALUES (1, ?, 'movie', ?, ?, ?)
                """,
                [
                    (10, "error", "2026-04-25T10:00:00.000Z", "boom"),
                    (11, "searched", "2026-04-25T11:00:00.000Z", None),
                ],
            )
            await conn.commit()
            result = await _active_errors(conn, [1])
        assert 1 not in result


# _recent_searches


class TestRecentSearches:
    @pytest.mark.asyncio()
    async def test_empty_log_returns_empty_list(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            result = await _recent_searches(conn, limit=5)
        assert result == []

    @pytest.mark.asyncio()
    async def test_returns_most_recent_within_limit(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.executemany(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, item_label, timestamp)
                VALUES (1, ?, 'movie', 'searched', ?, ?)
                """,
                [
                    (10, "Movie A", "2026-04-25T10:00:00.000Z"),
                    (11, "Movie B", "2026-04-25T11:00:00.000Z"),
                    (12, "Movie C", "2026-04-25T12:00:00.000Z"),
                ],
            )
            await conn.commit()
            result = await _recent_searches(conn, limit=2)
        assert len(result) == 2
        assert result[0]["item_label"] == "Movie C"
        assert result[1]["item_label"] == "Movie B"

    @pytest.mark.asyncio()
    async def test_rows_older_than_seven_days_excluded(self, seeded_instances: None) -> None:
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO search_log
                (instance_id, item_id, item_type, action, timestamp)
                VALUES (1, 10, 'movie', 'searched', datetime('now','-8 days'))
                """,
            )
            await conn.commit()
            result = await _recent_searches(conn, limit=5)
        assert result == []


# _cooldown_data


class TestCooldownData:
    @pytest.mark.asyncio()
    async def test_empty_instances_returns_empty(self, db: None) -> None:
        async with get_db() as conn:
            result = await _cooldown_data(conn, [])
        assert result == {}

    @pytest.mark.asyncio()
    async def test_instance_without_cooldowns_gets_empty_breakdown(
        self, seeded_instances: None
    ) -> None:
        async with get_db() as conn:
            async with conn.execute("SELECT * FROM instances WHERE id = 1") as cur:
                row = await cur.fetchone()
            assert row is not None
            result = await _cooldown_data(conn, [row])
        # Empty DB: breakdown is all zeros; unlocking_next is empty list.
        assert 1 in result
        bd = result[1]["cooldown_breakdown"]
        assert bd.get("missing", 0) == 0
        assert bd.get("cutoff", 0) == 0
        assert bd.get("upgrade", 0) == 0
        assert result[1]["unlocking_next"] == []

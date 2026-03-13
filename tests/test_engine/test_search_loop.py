"""Tests for the search loop engine — all HTTP calls mocked with respx."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import respx
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import Instance, InstanceType

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
# Valid Fernet key required wherever crypto.decrypt is called (supervisor tests)
MASTER_KEY: bytes = Fernet.generate_key()

_EPISODE_RECORD: dict[str, Any] = {
    "id": 101,
    "title": "Pilot",
    "seasonNumber": 1,
    "episodeNumber": 1,
    "airDateUtc": "2023-09-01T00:00:00Z",
    "series": {"title": "My Show"},
}

_MOVIE_RECORD: dict[str, Any] = {
    "id": 201,
    "title": "My Movie",
    "year": 2023,
    "digitalRelease": None,
}

_MISSING_SONARR = {"page": 1, "pageSize": 10, "totalRecords": 1, "records": [_EPISODE_RECORD]}
_MISSING_RADARR = {"page": 1, "pageSize": 10, "totalRecords": 1, "records": [_MOVIE_RECORD]}
_COMMAND_RESP = {"id": 1, "name": "EpisodeSearch"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_instance(
    *,
    instance_id: int = 1,
    itype: InstanceType = InstanceType.sonarr,
    url: str = SONARR_URL,
    batch_size: int = 10,
    hourly_cap: int = 20,
    cooldown_days: int = 7,
    unreleased_delay_hrs: int = 24,
    enabled: bool = True,
) -> Instance:
    return Instance(
        id=instance_id,
        name="Test Instance",
        type=itype,
        url=url,
        api_key="test-api-key",
        enabled=enabled,
        batch_size=batch_size,
        sleep_interval_mins=15,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=False,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed FK-required rows into instances so cooldowns can reference them.

    encrypted_api_key is set to a valid Fernet-encrypted value so that
    list_instances / get_instance can decrypt it without errors.
    """
    from houndarr.crypto import encrypt

    encrypted = encrypt("test-api-key", MASTER_KEY)
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", SONARR_URL, encrypted),
                (2, "Radarr Test", "radarr", RADARR_URL, encrypted),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Helper: fetch all search_log rows
# ---------------------------------------------------------------------------


async def _get_log_rows() -> list[dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM search_log ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests — items searched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_item_is_searched_when_not_on_cooldown(seeded_instances: None) -> None:
    """A fresh item with no cooldown record should be searched."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance()
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    rows = await _get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 101
    assert rows[0]["item_type"] == "episode"
    assert rows[0]["item_label"] == "My Show - S01E01 - Pilot"
    assert rows[0]["search_kind"] == "missing"


@pytest.mark.asyncio()
@respx.mock
async def test_radarr_item_is_searched(seeded_instances: None) -> None:
    """A Radarr missing movie should be searched with the movie item_type."""
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_RADARR)
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )

    instance = _make_instance(instance_id=2, itype=InstanceType.radarr, url=RADARR_URL)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    rows = await _get_log_rows()
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 201
    assert rows[0]["item_type"] == "movie"
    assert rows[0]["item_label"] == "My Movie (2023)"
    assert rows[0]["search_kind"] == "missing"


# ---------------------------------------------------------------------------
# Tests — items skipped (cooldown)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_item_skipped_when_on_cooldown(seeded_instances: None) -> None:
    """An item that was recently searched should be skipped."""
    # Pre-record a cooldown for episode 101
    from houndarr.services.cooldown import record_search

    await record_search(1, 101, "episode")

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR)
    )
    # search endpoint should NOT be called
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(cooldown_days=7)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    assert not search_route.called

    rows = await _get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "skipped"
    assert rows[0]["item_id"] == 101
    assert "cooldown" in (rows[0]["reason"] or "")


# ---------------------------------------------------------------------------
# Tests — hourly cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_hourly_cap_stops_searches(seeded_instances: None) -> None:
    """When the hourly cap is already reached, items should be skipped."""
    # Fill up the hourly cap by inserting recent successful missing-pass logs.
    async with get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO search_log (instance_id, item_id, item_type, search_kind, action, timestamp)
            VALUES (?, ?, 'episode', 'missing', 'searched', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            [(1, 900 + i) for i in range(5)],
        )
        await conn.commit()

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR)
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    # hourly_cap=5 — already used up
    instance = _make_instance(hourly_cap=5)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    assert not search_route.called

    rows = await _get_log_rows()
    assert rows[-1]["action"] == "skipped"
    assert "hourly cap" in (rows[-1]["reason"] or "")


@pytest.mark.asyncio()
@respx.mock
async def test_hourly_cap_zero_means_unlimited(seeded_instances: None) -> None:
    """hourly_cap=0 should disable the cap and allow all searches."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(hourly_cap=0)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1


# ---------------------------------------------------------------------------
# Tests — search_log rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_search_log_row_written_on_success(seeded_instances: None) -> None:
    """A successful search must produce a 'searched' log row with correct fields."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance()
    await run_instance_search(instance, MASTER_KEY)

    rows = await _get_log_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == 1
    assert row["item_id"] == 101
    assert row["item_type"] == "episode"
    assert row["item_label"] == "My Show - S01E01 - Pilot"
    assert row["search_kind"] == "missing"
    assert row["action"] == "searched"
    assert row["reason"] is None
    assert row["timestamp"] is not None


@pytest.mark.asyncio()
@respx.mock
async def test_unreleased_delay_skips_item_until_delay_elapses(seeded_instances: None) -> None:
    """Items inside unreleased delay are skipped with an explicit reason."""
    future_episode = {
        **_EPISODE_RECORD,
        "id": 303,
        "airDateUtc": "2999-01-01T00:00:00Z",
    }
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": [future_episode]})
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(unreleased_delay_hrs=36)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    assert not search_route.called
    rows = await _get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "skipped"
    assert rows[0]["reason"] == "unreleased delay (36h)"
    assert rows[0]["item_id"] == 303


@pytest.mark.asyncio()
@respx.mock
async def test_search_log_row_written_on_error(seeded_instances: None) -> None:
    """A failed search command must produce an 'error' log row."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_MISSING_SONARR)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(return_value=httpx.Response(500))

    instance = _make_instance()
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    rows = await _get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "error"
    assert rows[0]["item_id"] == 101


@pytest.mark.asyncio()
@respx.mock
async def test_no_log_rows_when_no_missing_items(seeded_instances: None) -> None:
    """An empty missing list should produce no log rows."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(
            200, json={"page": 1, "pageSize": 10, "totalRecords": 0, "records": []}
        )
    )

    instance = _make_instance()
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    rows = await _get_log_rows()
    assert rows == []


# ---------------------------------------------------------------------------
# Tests — supervisor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_supervisor_starts_and_stops_cleanly(db: None) -> None:
    """Supervisor with no instances should start and stop without error."""
    from houndarr.engine.supervisor import Supervisor

    sup = Supervisor(master_key=MASTER_KEY)
    await sup.start()
    await sup.stop()
    assert sup._tasks == {}  # noqa: SLF001


@pytest.mark.asyncio()
async def test_supervisor_stop_cancels_tasks(seeded_instances: None) -> None:
    """Supervisor tasks should be cancelled cleanly on stop()."""
    from houndarr.engine.supervisor import Supervisor

    # Patch run_instance_search to block indefinitely so we can test cancellation
    async def _block(*_: object, **__: object) -> int:
        import asyncio

        await asyncio.sleep(9999)
        return 0

    with patch(
        "houndarr.engine.supervisor.run_instance_search",
        new=AsyncMock(side_effect=_block),
    ):
        sup = Supervisor(master_key=MASTER_KEY)
        await sup.start()
        # Give the tasks a moment to start and enter their sleep
        import asyncio

        await asyncio.sleep(0.05)
        await sup.stop()

    assert sup._tasks == {}  # noqa: SLF001


@pytest.mark.asyncio()
async def test_supervisor_stop_completes_within_timeout(seeded_instances: None) -> None:
    """stop() must complete well within the 10-second shutdown timeout."""
    import asyncio
    import time

    from houndarr.engine.supervisor import Supervisor

    async def _block(*_: object, **__: object) -> int:
        await asyncio.sleep(9999)
        return 0

    with patch(
        "houndarr.engine.supervisor.run_instance_search",
        new=AsyncMock(side_effect=_block),
    ):
        sup = Supervisor(master_key=MASTER_KEY)
        await sup.start()
        await asyncio.sleep(0.05)

        t0 = time.monotonic()
        await sup.stop()
        elapsed = time.monotonic() - t0

    # Should complete far below the 10s timeout (tasks cancel immediately)
    assert elapsed < 5.0
    assert sup._tasks == {}  # noqa: SLF001


@pytest.mark.asyncio()
async def test_supervisor_reconcile_starts_task_for_enabled_instance(db: None) -> None:
    """reconcile_instance() should start a task when an enabled instance is added."""
    from houndarr.crypto import encrypt
    from houndarr.engine.supervisor import Supervisor

    with patch(
        "houndarr.engine.supervisor.run_instance_search",
        new=AsyncMock(return_value=0),
    ):
        sup = Supervisor(master_key=MASTER_KEY)
        await sup.start()
        assert sup._tasks == {}  # noqa: SLF001

        encrypted = encrypt("test-api-key", MASTER_KEY)
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO instances (id, name, type, url, encrypted_api_key, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, "Sonarr Test", "sonarr", SONARR_URL, encrypted, 1),
            )
            await conn.commit()

        await sup.reconcile_instance(1)
        assert 1 in sup._tasks  # noqa: SLF001

        await sup.stop()


@pytest.mark.asyncio()
async def test_supervisor_reconcile_stops_task_when_instance_disabled(
    seeded_instances: None,
) -> None:
    """reconcile_instance() should cancel an existing task after disable."""
    import asyncio

    from houndarr.engine.supervisor import Supervisor

    async def _block(*_: object, **__: object) -> int:
        await asyncio.sleep(9999)
        return 0

    with patch(
        "houndarr.engine.supervisor.run_instance_search",
        new=AsyncMock(side_effect=_block),
    ):
        sup = Supervisor(master_key=MASTER_KEY)
        await sup.start()
        assert 1 in sup._tasks  # noqa: SLF001

        async with get_db() as conn:
            await conn.execute("UPDATE instances SET enabled = 0 WHERE id = ?", (1,))
            await conn.commit()

        await sup.reconcile_instance(1)
        assert 1 not in sup._tasks  # noqa: SLF001

        await sup.stop()


@pytest.mark.asyncio()
async def test_trigger_run_now_deduplicates_pending_manual_runs(
    seeded_instances: None,
) -> None:
    """trigger_run_now() should keep at most one pending manual run per instance."""
    import asyncio

    from houndarr.engine.supervisor import Supervisor

    gate = asyncio.Event()

    async def _block(*_: object, **__: object) -> int:
        await gate.wait()
        return 0

    with patch(
        "houndarr.engine.supervisor.run_instance_search",
        new=AsyncMock(side_effect=_block),
    ):
        sup = Supervisor(master_key=MASTER_KEY)
        await sup.start()

        status_1 = await sup.trigger_run_now(1)
        status_2 = await sup.trigger_run_now(1)

        assert status_1 == "accepted"
        assert status_2 == "accepted"
        assert len(sup._manual_runs) == 1  # noqa: SLF001

        gate.set()
        await asyncio.sleep(0)
        await sup.stop()


# ---------------------------------------------------------------------------
# Tests — cutoff-unmet pass
# ---------------------------------------------------------------------------

_CUTOFF_SONARR = {"page": 1, "pageSize": 5, "totalRecords": 1, "records": [_EPISODE_RECORD]}
_CUTOFF_RADARR = {"page": 1, "pageSize": 5, "totalRecords": 1, "records": [_MOVIE_RECORD]}


def _make_cutoff_instance(
    *,
    instance_id: int = 1,
    itype: InstanceType = InstanceType.sonarr,
    url: str = SONARR_URL,
    cutoff_enabled: bool = True,
    cutoff_batch_size: int = 5,
    hourly_cap: int = 20,
    cutoff_hourly_cap: int = 1,
    cooldown_days: int = 7,
    cutoff_cooldown_days: int = 21,
    unreleased_delay_hrs: int = 24,
) -> Instance:
    return Instance(
        id=instance_id,
        name="Cutoff Test",
        type=itype,
        url=url,
        api_key="test-api-key",
        enabled=True,
        batch_size=10,
        sleep_interval_mins=15,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=cutoff_enabled,
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_runs_when_enabled(seeded_instances: None) -> None:
    """When cutoff_enabled=True the cutoff-unmet endpoint is called and items are searched."""
    # Missing pass returns nothing; cutoff pass has one item
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_SONARR)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_cutoff_instance(cutoff_enabled=True)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    rows = await _get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 101
    assert rows[0]["search_kind"] == "cutoff"


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_hourly_cap_is_separate_from_missing_hourly_cap(
    seeded_instances: None,
) -> None:
    """Cutoff and missing passes should not share hourly cap budget."""
    missing_one = {"records": [{**_EPISODE_RECORD, "id": 401}]}
    cutoff_one = {"records": [{**_EPISODE_RECORD, "id": 402}]}

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_one)
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=cutoff_one)
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_cutoff_instance(
        cutoff_enabled=True,
        hourly_cap=1,
        cutoff_hourly_cap=1,
        cooldown_days=0,
        cutoff_cooldown_days=0,
    )
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 2
    assert search_route.called
    assert len(search_route.calls) == 2

    rows = await _get_log_rows()
    searched_rows = [row for row in rows if row["action"] == "searched"]
    assert len(searched_rows) == 2
    assert searched_rows[0]["search_kind"] == "missing"
    assert searched_rows[1]["search_kind"] == "cutoff"


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_respects_cooldown_from_missing_pass(seeded_instances: None) -> None:
    """An item searched in missing pass should be skipped in cutoff pass."""
    missing_with_one = {"records": [_EPISODE_RECORD]}
    cutoff_with_same = {"records": [_EPISODE_RECORD]}

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_with_one)
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=cutoff_with_same)
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_cutoff_instance(cutoff_enabled=True)
    count = await run_instance_search(instance, MASTER_KEY)

    # Missing pass searches once; cutoff pass sees cooldown and skips duplicate.
    assert count == 1
    assert search_route.called
    assert len(search_route.calls) == 1

    rows = await _get_log_rows()
    assert len(rows) == 2
    assert rows[0]["action"] == "searched"
    assert rows[1]["action"] == "skipped"
    assert "cooldown" in (rows[1]["reason"] or "")


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_uses_cutoff_cooldown_setting(seeded_instances: None) -> None:
    """Cutoff pass should honor cutoff_cooldown_days instead of missing cooldown_days."""
    from houndarr.services.cooldown import record_search

    await record_search(1, 101, "episode")

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json={"records": [_EPISODE_RECORD]})
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_cutoff_instance(
        cutoff_enabled=True,
        cooldown_days=0,
        cutoff_cooldown_days=21,
    )
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    assert not search_route.called
    rows = await _get_log_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "skipped"
    assert rows[0]["reason"] == "on cutoff cooldown (21d)"


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_skipped_when_disabled(seeded_instances: None) -> None:
    """When cutoff_enabled=False the cutoff endpoint must never be called."""
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    cutoff_route = respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_SONARR)
    )

    instance = _make_cutoff_instance(cutoff_enabled=False)
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 0
    assert not cutoff_route.called


@pytest.mark.asyncio()
@respx.mock
async def test_cutoff_pass_radarr(seeded_instances: None) -> None:
    """Radarr cutoff-unmet items are searched with movie item_type."""
    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=_CUTOFF_RADARR)
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 2})
    )

    instance = _make_cutoff_instance(
        instance_id=2, itype=InstanceType.radarr, url=RADARR_URL, cutoff_enabled=True
    )
    count = await run_instance_search(instance, MASTER_KEY)

    assert count == 1
    rows = await _get_log_rows()
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 201
    assert rows[0]["item_type"] == "movie"

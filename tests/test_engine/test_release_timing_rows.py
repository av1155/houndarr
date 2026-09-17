"""Which search_log rows may arm or cancel the release-timing retry (issue #770).

Two shapes are covered.  A skip written by a gate other than release
timing, such as the hourly cap, must leave a pending retry alone.  And in
season, artist, or author mode, where every row carries the parent's
synthetic id, a wanted item still inside its post-release grace window
must not re-arm the parent's retry on every cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from houndarr.database import get_db
from houndarr.engine import candidates as candidates_mod
from houndarr.engine import search_loop
from houndarr.engine.adapters.sonarr import _season_item_id
from houndarr.engine.search_loop import run_instance_search
from houndarr.enums import CycleTrigger
from houndarr.repositories import cooldowns as cooldowns_repo
from houndarr.repositories import search_log as search_log_repo
from houndarr.services.instances import InstanceType, LidarrSearchMode, SonarrSearchMode

from .conftest import (
    _COMMAND_RESP,
    _EPISODE_RECORD,
    _MOVIE_RECORD,
    LIDARR_URL,
    MASTER_KEY,
    RADARR_URL,
    SONARR_URL,
    get_log_rows,
    insert_search_log_row,
    make_instance,
    seed_release_timing_retry,
)

# Rows take their timestamp from SQLite's own clock, so the pinned clock has to
# track the real one for elapsed-time comparisons to mean anything.
_NOW = datetime.now(UTC)


def _freeze_now(monkeypatch: pytest.MonkeyPatch, now: datetime = _NOW) -> None:
    """Pin every clock the retry decision reads, including the release-date checks."""

    class _PinnedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    monkeypatch.setattr(search_loop, "datetime", _PinnedDatetime)
    monkeypatch.setattr(candidates_mod, "datetime", _PinnedDatetime)
    monkeypatch.setattr(search_log_repo, "datetime", _PinnedDatetime)
    monkeypatch.setattr(cooldowns_repo, "_now_utc", lambda: now)


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


async def _insert_grace_row(item_id: int, at: datetime) -> None:
    """Write a parent grace skip at an explicit time, which the engine cannot do."""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO search_log
                (instance_id, item_id, item_type, search_kind, action, reason, timestamp)
            VALUES (1, ?, 'episode', 'missing', 'skipped', 'post-release grace (6h)', ?)
            """,
            (item_id, _iso(at)),
        )
        await conn.commit()


def _page(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"page": 1, "pageSize": 50, "totalRecords": len(records), "records": records}


def _sonarr(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "instance_id": 1,
        "itype": InstanceType.sonarr,
        "batch_size": 1,
        "hourly_cap": 20,
        "cooldown_days": 7,
        "post_release_grace_hrs": 6,
    }
    defaults.update(overrides)
    return make_instance(**defaults)


def _episode(episode_id: int, air_at: datetime, number: int) -> dict[str, Any]:
    return {
        **_EPISODE_RECORD,
        "id": episode_id,
        "episodeNumber": number,
        "airDateUtc": _iso(air_at),
    }


# ---------------------------------------------------------------------------
# An hourly-limit skip must not cancel a pending retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "later_reason",
    ["hourly limit reached (20/hr)", "tag filter (excluded tag)", "on cooldown (7d)"],
)
@pytest.mark.asyncio()
@respx.mock
async def test_gate_skip_does_not_cancel_pending_retry(
    seeded_instances: None,
    later_reason: str,
) -> None:
    """A newer skip from another gate leaves the release-timing retry pending."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="post-release grace (6h)",
    )
    await insert_search_log_row(
        instance_id=1,
        item_id=101,
        item_type="episode",
        search_kind="missing",
        action="skipped",
        reason=later_reason,
    )

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_EPISODE_RECORD])),
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )

    assert await run_instance_search(_sonarr(post_release_grace_hrs=0), MASTER_KEY) == 1
    assert search_route.called


@pytest.mark.parametrize(
    "later_reason",
    ["radarr reports not available", "radarr status indicates unreleased"],
)
@pytest.mark.asyncio()
@respx.mock
async def test_availability_skip_still_cancels_a_pending_retry(
    seeded_instances: None,
    later_reason: str,
) -> None:
    """A movie Radarr called unavailable waits for its cooldown, not an early retry."""
    await seed_release_timing_retry(
        instance_id=2,
        item_id=201,
        item_type="movie",
        reason="post-release grace (6h)",
    )
    await insert_search_log_row(
        instance_id=2,
        item_id=201,
        item_type="movie",
        search_kind="missing",
        action="skipped",
        reason=later_reason,
    )

    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_MOVIE_RECORD])),
    )
    search_route = respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )
    inst = make_instance(
        instance_id=2,
        itype=InstanceType.radarr,
        batch_size=1,
        hourly_cap=20,
        cooldown_days=7,
        post_release_grace_hrs=0,
    )

    assert await run_instance_search(inst, MASTER_KEY) == 0
    assert not search_route.called


@pytest.mark.asyncio()
@respx.mock
async def test_retry_stays_one_shot_after_it_fires(
    seeded_instances: None,
) -> None:
    """The retry's own searched row still ends it, even with a cap row in between."""
    await seed_release_timing_retry(
        instance_id=1,
        item_id=101,
        item_type="episode",
        reason="post-release grace (6h)",
    )
    await insert_search_log_row(
        instance_id=1,
        item_id=101,
        item_type="episode",
        search_kind="missing",
        action="skipped",
        reason="hourly limit reached (20/hr)",
    )

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_EPISODE_RECORD])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )
    inst = _sonarr(post_release_grace_hrs=0)

    assert await run_instance_search(inst, MASTER_KEY) == 1
    assert await run_instance_search(inst, MASTER_KEY) == 0


# ---------------------------------------------------------------------------
# A sibling inside post-release grace must not re-arm the parent
# ---------------------------------------------------------------------------


def _mock_season_pages(records: list[dict[str, Any]]) -> respx.Route:
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page(records)),
    )
    return respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )


@pytest.mark.asyncio()
@respx.mock
async def test_sibling_in_grace_does_not_re_arm_the_parent_each_cycle(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Season mode searches the parent once while a sibling is still inside grace."""
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    search_route = _mock_season_pages([aired, in_grace])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    for _ in range(4):
        await run_instance_search(inst, MASTER_KEY)

    assert search_route.call_count == 1
    rows = await get_log_rows()
    assert [r["item_id"] for r in rows if r["action"] == "searched"] == [_season_item_id(55, 1)]


@pytest.mark.asyncio()
@respx.mock
async def test_parent_retries_once_after_the_sibling_grace_has_elapsed(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the sibling's grace can no longer be open, the parent gets its one retry."""
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    search_route = _mock_season_pages([aired, in_grace])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    await run_instance_search(inst, MASTER_KEY)
    await run_instance_search(inst, MASTER_KEY)
    assert search_route.call_count == 1

    later = _NOW + timedelta(hours=7)
    _freeze_now(monkeypatch, later)
    _mock_season_pages([aired, _episode(102, _NOW - timedelta(hours=1), 2)])

    assert await run_instance_search(inst, MASTER_KEY) == 1
    assert await run_instance_search(inst, MASTER_KEY) == 0


@pytest.mark.asyncio()
@respx.mock
async def test_lone_record_still_retries_after_its_own_grace(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no sibling, a season whose only record left grace still retries once."""
    _freeze_now(monkeypatch)
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])
    await run_instance_search(inst, MASTER_KEY)

    _mock_season_pages([_episode(101, _NOW - timedelta(minutes=30), 1)])
    assert await run_instance_search(inst, MASTER_KEY) == 0

    _freeze_now(monkeypatch, _NOW + timedelta(hours=7))
    _mock_season_pages([_episode(101, _NOW - timedelta(minutes=30), 1)])
    assert await run_instance_search(inst, MASTER_KEY) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_keeps_searching_while_a_sibling_is_in_grace(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run now bypasses grace at the gate, so it also bypasses the sibling wait."""
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    search_route = _mock_season_pages([aired, in_grace])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    # The first cycle fills the batch on the released record, so the sibling
    # only logs its grace skip on the second one.
    await run_instance_search(inst, MASTER_KEY)
    await run_instance_search(inst, MASTER_KEY)
    assert search_route.call_count == 1

    await run_instance_search(inst, MASTER_KEY, cycle_trigger=CycleTrigger.run_now)

    assert search_route.call_count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_item_mode_retry_is_not_delayed_by_its_own_grace_rows(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Episode mode keeps the immediate retry: the candidate itself cleared the gate."""
    _freeze_now(monkeypatch)
    inst = _sonarr()
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_episode(101, _NOW - timedelta(days=30), 1)])),
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )
    await run_instance_search(inst, MASTER_KEY)

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_episode(101, _NOW - timedelta(hours=1), 1)])),
    )
    assert await run_instance_search(inst, MASTER_KEY) == 0

    _freeze_now(monkeypatch, _NOW + timedelta(hours=7))
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_episode(101, _NOW - timedelta(hours=1), 1)])),
    )
    assert await run_instance_search(inst, MASTER_KEY) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_artist_mode_sibling_in_grace_holds_the_parent(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lidarr artist mode shares the season-mode behaviour."""
    _freeze_now(monkeypatch)

    def _album(album_id: int, release_at: datetime) -> dict[str, Any]:
        return {
            "id": album_id,
            "artistId": 50,
            "title": f"Album {album_id}",
            "releaseDate": _iso(release_at),
            "artist": {"id": 50, "artistName": "Test Artist"},
        }

    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(
            200,
            json=_page(
                [_album(301, _NOW - timedelta(days=30)), _album(302, _NOW - timedelta(hours=1))]
            ),
        ),
    )
    search_route = respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json={"id": 3}),
    )
    inst = make_instance(
        instance_id=3,
        itype=InstanceType.lidarr,
        batch_size=1,
        hourly_cap=20,
        cooldown_days=7,
        post_release_grace_hrs=6,
        lidarr_search_mode=LidarrSearchMode.artist_context,
    )

    for _ in range(3):
        await run_instance_search(inst, MASTER_KEY)

    assert search_route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_a_later_sibling_grace_holds_the_parent_past_the_first_one(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Records entering grace one after another each push the wait out."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW + timedelta(hours=1))
    await _insert_grace_row(parent, _NOW + timedelta(hours=5))

    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)
    search_route = _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])

    # The first row's window has closed by this point; the second one's has not.
    _freeze_now(monkeypatch, _NOW + timedelta(hours=7, minutes=30))
    assert await run_instance_search(inst, MASTER_KEY) == 0
    assert search_route.call_count == 0

    _freeze_now(monkeypatch, _NOW + timedelta(hours=11, minutes=30))
    assert await run_instance_search(inst, MASTER_KEY) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retry_window_also_waits_for_a_sibling_in_grace(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enabled hot retry window does not re-search the parent every interval."""
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    search_route = _mock_season_pages([aired, in_grace])
    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        missing_hot_retry_window_hrs=24,
        missing_hot_retry_interval_hrs=1,
    )

    await run_instance_search(inst, MASTER_KEY)
    await run_instance_search(inst, MASTER_KEY)
    assert search_route.call_count == 1

    # The retry interval has elapsed, but the sibling's grace window may not have.
    _freeze_now(monkeypatch, _NOW + timedelta(hours=2))
    assert await run_instance_search(inst, MASTER_KEY) == 0

    _freeze_now(monkeypatch, _NOW + timedelta(hours=7))
    assert await run_instance_search(inst, MASTER_KEY) == 1

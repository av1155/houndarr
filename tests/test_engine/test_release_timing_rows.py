"""Which search_log rows may arm or cancel the release-timing retry.

Three shapes are covered.  A skip written by a gate other than release
timing, such as the hourly cap, must leave a pending retry alone (#770).
And in season, artist, or author mode, where every row carries the
parent's synthetic id, a wanted record must not re-arm the parent's
retry on every cycle: one inside its post-release grace window is held
for a bounded wait (#770), and one this host still reads as unreleased
is dropped when another record of that parent cleared the gate (#782).
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


async def _insert_item_row(action: str, reason: str | None, at: datetime) -> None:
    """Write a row for episode 101 at an explicit time, which the engine cannot do."""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO search_log
                (instance_id, item_id, item_type, search_kind, action, reason, timestamp)
            VALUES (1, 101, 'episode', 'missing', ?, ?, ?)
            """,
            (action, reason, _iso(at)),
        )
        await conn.commit()


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
    [
        "hourly limit reached (20/hr)",
        "tag filter (excluded tag)",
        "on cooldown (7d)",
        "waiting on post-release grace (6h)",
    ],
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
async def test_retry_survives_a_hot_retry_window_that_never_searched(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interval longer than the window leaves the one retry pending, not cancelled."""
    _freeze_now(monkeypatch)
    from houndarr.services.cooldown import record_search

    await record_search(1, 101, "episode")
    await _insert_item_row("searched", None, _NOW - timedelta(hours=6))
    await _insert_item_row("skipped", "post-release grace (1h)", _NOW - timedelta(hours=5))
    await _insert_item_row("skipped", "in hot retry window (2h)", _NOW - timedelta(hours=4))

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=_page([_EPISODE_RECORD])),
    )
    search_route = respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP),
    )
    inst = _sonarr(
        post_release_grace_hrs=1,
        missing_hot_retry_window_hrs=2,
        missing_hot_retry_interval_hrs=6,
    )

    assert await run_instance_search(inst, MASTER_KEY) == 1
    assert await run_instance_search(inst, MASTER_KEY) == 0
    assert search_route.call_count == 1


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

    # The episode leaves its own grace 5h from now, an hour before the group
    # wait would end. Item mode must not serve that wait.
    _freeze_now(monkeypatch, _NOW + timedelta(hours=5, minutes=30))
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


@pytest.mark.asyncio()
@respx.mock
async def test_hot_retries_resume_once_the_sibling_leaves_grace(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grace rows older than the parent's own search no longer hold the window shut."""
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    search_route = _mock_season_pages([aired, in_grace])
    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        missing_hot_retry_window_hrs=48,
        missing_hot_retry_interval_hrs=1,
    )

    await run_instance_search(inst, MASTER_KEY)
    await run_instance_search(inst, MASTER_KEY)

    # The sibling is out of grace from here on, so no new grace rows land and
    # the parent's own search becomes the newest row for the key.
    for hours in (7, 9, 11):
        _freeze_now(monkeypatch, _NOW + timedelta(hours=hours))
        _mock_season_pages([aired, _episode(102, _NOW - timedelta(hours=1), 2)])
        assert await run_instance_search(inst, MASTER_KEY) == 1

    assert search_route.call_count == 4


@pytest.mark.parametrize(
    ("grace_hrs", "expected"),
    [pytest.param(2, 1, id="lowered"), pytest.param(48, 0, id="raised")],
)
@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_follows_the_current_grace_setting(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
    grace_hrs: int,
    expected: int,
) -> None:
    """Changing the setting re-reads the bound; rows keep only their timestamp."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW)

    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        post_release_grace_hrs=grace_hrs,
    )
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])

    _freeze_now(monkeypatch, _NOW + timedelta(hours=6))
    assert await run_instance_search(inst, MASTER_KEY) == expected


@pytest.mark.parametrize(
    ("grace_hrs", "row_age_hrs", "expected"),
    [
        pytest.param(0, 0, 1, id="disabled"),
        pytest.param(1, 0, 0, id="one-hour-holds"),
        pytest.param(1, 2, 1, id="one-hour-elapsed"),
    ],
)
@pytest.mark.asyncio()
@respx.mock
async def test_the_shortest_grace_windows(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
    grace_hrs: int,
    row_age_hrs: int,
    expected: int,
) -> None:
    """Zero turns the wait off; one hour is a real window, not a rounding of zero."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW - timedelta(hours=row_age_hrs))

    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        post_release_grace_hrs=grace_hrs,
    )
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])

    assert await run_instance_search(inst, MASTER_KEY) == expected


@pytest.mark.parametrize(
    ("row_age_hrs", "expected"),
    [pytest.param(11, 0, id="just-inside"), pytest.param(13, 1, id="just-outside")],
)
@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_is_one_grace_window_long(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
    row_age_hrs: int,
    expected: int,
) -> None:
    """Brackets the wait at one window, the length the skip-reasons page documents."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW - timedelta(hours=row_age_hrs))

    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        post_release_grace_hrs=12,
    )
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])

    assert await run_instance_search(inst, MASTER_KEY) == expected


# ---------------------------------------------------------------------------
# A record this host still reads as unreleased must not speak for its parent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_a_record_the_arr_calls_released_does_not_re_search_the_parent(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The season is searched once, the same as when the two clocks agree.

    Sonarr keeps unreleased episodes out of the wanted list, so one
    appears here only while this host's clock trails Sonarr's.  The
    skip lands under the season's id, which used to read as the
    season's own release state and re-arm its retry every cycle.
    """
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    still_future_here = _episode(102, _NOW + timedelta(minutes=5), 2)
    search_route = _mock_season_pages([aired, still_future_here])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    for _ in range(6):
        await run_instance_search(inst, MASTER_KEY)

    assert search_route.call_count == 1
    rows = await get_log_rows()
    assert not any(r["reason"] == "not yet released" for r in rows)


@pytest.mark.asyncio()
@respx.mock
async def test_artist_mode_shares_the_trailing_clock_behaviour(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lidarr artist mode behaves the same as Sonarr season mode."""
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
                [_album(301, _NOW - timedelta(days=30)), _album(302, _NOW + timedelta(minutes=5))]
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

    for _ in range(6):
        await run_instance_search(inst, MASTER_KEY)

    assert search_route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_a_season_no_record_represented_still_logs_the_block(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing else to search, the season's own block is still recorded."""
    _freeze_now(monkeypatch)
    _mock_season_pages([_episode(101, _NOW + timedelta(minutes=5), 1)])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    assert await run_instance_search(inst, MASTER_KEY) == 0

    rows = await get_log_rows()
    assert rows
    assert {r["reason"] for r in rows} == {"not yet released"}
    assert {r["item_id"] for r in rows} == {_season_item_id(55, 1)}


@pytest.mark.asyncio()
@respx.mock
async def test_a_season_takes_its_retry_once_its_blocked_record_releases(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row a season did write still arms the one retry it is there for."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    _mock_season_pages([_episode(101, _NOW + timedelta(minutes=5), 1)])
    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        post_release_grace_hrs=0,
    )

    assert await run_instance_search(inst, MASTER_KEY) == 0

    released = _NOW + timedelta(minutes=10)
    _freeze_now(monkeypatch, released)
    _mock_season_pages([_episode(101, _NOW + timedelta(minutes=5), 1)])

    assert await run_instance_search(inst, MASTER_KEY) == 1
    assert await run_instance_search(inst, MASTER_KEY) == 0


@pytest.mark.asyncio()
@respx.mock
async def test_episode_mode_logs_a_blocked_record_straight_away(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item-level modes keep writing the row as they are reached."""
    _freeze_now(monkeypatch)
    _mock_season_pages(
        [_episode(101, _NOW + timedelta(minutes=5), 1), _episode(102, _NOW - timedelta(days=30), 2)]
    )
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.episode)

    assert await run_instance_search(inst, MASTER_KEY) == 1

    rows = await get_log_rows()
    assert rows[0]["reason"] == "not yet released"
    assert rows[0]["item_id"] == 101


@pytest.mark.asyncio()
@respx.mock
async def test_run_now_drops_a_held_row_for_a_season_it_searched(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual run holds and drops the row the same way a scheduled one does.

    The blocked record comes first so the gate reaches it before the
    batch fills on the released one.  That the pre-release check still
    applies under run now is pinned by
    ``test_release_timing.test_run_now_does_not_bypass_unreleased``; in
    season mode both records would dispatch the same season search, so
    this case cannot see that on its own.
    """
    _freeze_now(monkeypatch)
    still_future_here = _episode(101, _NOW + timedelta(minutes=5), 1)
    aired = _episode(102, _NOW - timedelta(days=30), 2)
    search_route = _mock_season_pages([still_future_here, aired])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    await run_instance_search(inst, MASTER_KEY, cycle_trigger=CycleTrigger.run_now)

    assert search_route.call_count == 1
    rows = await get_log_rows()
    assert [r["action"] for r in rows] == ["searched"]


@pytest.mark.asyncio()
@respx.mock
async def test_a_downloading_record_still_counts_as_clearing_the_gate(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The season's cycle reads as the download it is waiting on, nothing else.

    The released record clears the release gate and then hands the group
    slot back because it is already downloading, so a held row keyed on
    that slot would land again and re-arm the season.
    """
    from tests.conftest import serve_download_queue

    _freeze_now(monkeypatch)
    still_future_here = _episode(101, _NOW + timedelta(minutes=5), 1)
    downloading = _episode(102, _NOW - timedelta(days=30), 2)
    search_route = _mock_season_pages([still_future_here, downloading])
    serve_download_queue([{"id": 900102, "episodeId": 102}])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    assert await run_instance_search(inst, MASTER_KEY) == 0

    assert not search_route.called
    assert {r["reason"] for r in await get_log_rows()} == {"already in download queue"}


# ---------------------------------------------------------------------------
# The wait says so in the log instead of reading as an ordinary cooldown (#783)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_a_held_parent_names_the_wait_instead_of_its_cooldown(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row says the parent is waiting, not that it is merely pacing."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW - timedelta(hours=1))
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    assert await run_instance_search(inst, MASTER_KEY) == 0

    rows = await get_log_rows()
    reasons = [r["reason"] for r in rows if r["action"] == "skipped"]
    assert "waiting on post-release grace (6h)" in reasons
    assert not any(r is not None and r.startswith("on cooldown") for r in reasons)

    # The row has to reach the operator the way every other skip does: inside
    # the cycle's card, and not behind the Logs page's Hide system switch.
    held = next(r for r in rows if r["reason"] == "waiting on post-release grace (6h)")
    assert held["cycle_trigger"] == "scheduled"
    assert held["cycle_id"]
    assert held["search_kind"] == "missing"
    assert held["item_id"] == parent
    assert held["item_label"]


@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_is_not_muted_by_the_parents_own_cooldown_row(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait needs a log throttle of its own, not the cooldown's.

    Driven the way a real instance reaches the wait.  The cycle after
    the search has nothing to hold yet, because the sibling's grace row
    lands later in that same pass, so it writes the ordinary cooldown
    row and claims that throttle key.  Sharing the key would suppress
    the first genuinely held cycle for a day and leave the parent
    sitting on a stale cooldown row, which is the whole of #783.
    """
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    _mock_season_pages([aired, in_grace])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    for _ in range(3):
        await run_instance_search(inst, MASTER_KEY)

    parent = _season_item_id(55, 1)
    reasons = [
        r["reason"]
        for r in await get_log_rows()
        if r["action"] == "skipped" and r["item_id"] == parent
    ]
    assert "on cooldown (7d)" in reasons
    assert "waiting on post-release grace (6h)" in reasons


@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_is_not_muted_by_the_parents_own_hot_retry_row(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other row a held parent can write first must not mute it either.

    With the hot retry window on, the parent writes
    ``in hot retry window (Nh)`` while its interval is unelapsed, and is
    held once the interval passes but the sibling's grace has not.  Both
    land for the same parent inside one throttle window, so the wait
    needs a key of its own against that row too, not only the cooldown's.
    """
    _freeze_now(monkeypatch)
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    in_grace = _episode(102, _NOW - timedelta(hours=1), 2)
    _mock_season_pages([aired, in_grace])
    inst = _sonarr(
        sonarr_search_mode=SonarrSearchMode.season_context,
        missing_hot_retry_window_hrs=24,
        missing_hot_retry_interval_hrs=2,
    )

    # Search, then the cycle that first logs the sibling's grace row.
    await run_instance_search(inst, MASTER_KEY)
    await run_instance_search(inst, MASTER_KEY)

    # Anchored on that row now, but inside the retry interval.
    _freeze_now(monkeypatch, _NOW + timedelta(hours=1))
    _mock_season_pages([aired, in_grace])
    assert await run_instance_search(inst, MASTER_KEY) == 0

    # Interval elapsed, so the wait is what holds it back.
    _freeze_now(monkeypatch, _NOW + timedelta(hours=3))
    _mock_season_pages([aired, in_grace])
    assert await run_instance_search(inst, MASTER_KEY) == 0

    parent = _season_item_id(55, 1)
    reasons = [
        r["reason"]
        for r in await get_log_rows()
        if r["action"] == "skipped" and r["item_id"] == parent
    ]
    assert "in hot retry window (24h)" in reasons
    assert "waiting on post-release grace (6h)" in reasons


@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_names_the_current_grace_setting(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window in the reason is the instance's, the way the other rows read."""
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW - timedelta(hours=1))
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context, post_release_grace_hrs=48)

    assert await run_instance_search(inst, MASTER_KEY) == 0

    reasons = [r["reason"] for r in await get_log_rows() if r["action"] == "skipped"]
    assert "waiting on post-release grace (48h)" in reasons


@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_writes_one_row_not_one_per_cycle(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Throttled under its own bucket, the way the other per-cycle rows are."""
    from houndarr.services.cooldown import _reset_skip_log_cache, record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW - timedelta(hours=1))
    _mock_season_pages([_episode(101, _NOW - timedelta(days=30), 1)])
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context)

    for _ in range(4):
        await run_instance_search(inst, MASTER_KEY)

    def _held() -> int:
        return sum(1 for r in rows if r["reason"] == "waiting on post-release grace (6h)")

    rows = await get_log_rows()
    assert _held() == 1

    _reset_skip_log_cache()
    await run_instance_search(inst, MASTER_KEY)
    rows = await get_log_rows()
    assert _held() == 2


@pytest.mark.asyncio()
@respx.mock
async def test_the_wait_still_ends_once_the_window_has_passed(
    seeded_instances: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hold row must not outrank the grace row that arms the retry.

    ``fetch_latest_missing_reason`` returns the newest row it accepts,
    and the hold reason is not one it accepts.  Were it, it would
    outrank the grace row and read as "not a release-timing block", so
    the parent would lose the retry entirely rather than wait for it.

    The other half of the trap, that a hold row must not feed the wait's
    own bound, cannot be driven from here: the log throttle reads an
    unpatched clock, so only one hold row lands however far the frozen
    clock moves.  It is pinned against the query instead, in
    ``test_search_log.test_the_group_hold_row_does_not_feed_the_wait_it_records``.
    """
    from houndarr.services.cooldown import record_search

    parent = _season_item_id(55, 1)
    _freeze_now(monkeypatch)
    await record_search(1, parent, "episode")
    await _insert_grace_row(parent, _NOW - timedelta(hours=1))
    aired = _episode(101, _NOW - timedelta(days=30), 1)
    inst = _sonarr(sonarr_search_mode=SonarrSearchMode.season_context, post_release_grace_hrs=48)

    for hours in (0, 25):
        _freeze_now(monkeypatch, _NOW + timedelta(hours=hours))
        _mock_season_pages([aired])
        assert await run_instance_search(inst, MASTER_KEY) == 0

    _freeze_now(monkeypatch, _NOW + timedelta(hours=49))
    _mock_season_pages([aired])

    assert await run_instance_search(inst, MASTER_KEY) == 1

"""Golden tests for search_log row sequences.

These tests capture the exact ``search_log`` output for known input scenarios
and prove that the Phase 1–2 refactor (adapter pattern + unified pipeline)
produces bit-identical results to the pre-refactor engine.

Each test asserts the complete row sequence - field values and ordering - for
a multi-step search cycle.  They are regression snapshots, not behavioural
unit tests, and should break only when someone intentionally changes search
engine output.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import (
    Instance,
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrSearchMode,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
LIDARR_URL = "http://lidarr:8686"
READARR_URL = "http://readarr:8787"
WHISPARR_URL = "http://whisparr:6969"
MASTER_KEY: bytes = Fernet.generate_key()

# Two distinct Sonarr episodes - same series, different seasons.
_EP_S01E01: dict[str, Any] = {
    "id": 101,
    "seriesId": 55,
    "title": "Pilot",
    "seasonNumber": 1,
    "episodeNumber": 1,
    "airDateUtc": "2023-01-10T00:00:00Z",
    "series": {"title": "My Show"},
}
_EP_S02E01: dict[str, Any] = {
    "id": 102,
    "seriesId": 55,
    "title": "Premiere",
    "seasonNumber": 2,
    "episodeNumber": 1,
    "airDateUtc": "2023-06-15T00:00:00Z",
    "series": {"title": "My Show"},
}

# Two Radarr movies - one released, one released.
_MOVIE_A: dict[str, Any] = {
    "id": 201,
    "title": "Alpha Movie",
    "year": 2023,
    "status": "released",
    "minimumAvailability": "released",
    "isAvailable": True,
    "inCinemas": "2023-01-01T00:00:00Z",
    "physicalRelease": "2023-04-01T00:00:00Z",
    "releaseDate": "2023-04-01T00:00:00Z",
    "digitalRelease": None,
}
_MOVIE_B: dict[str, Any] = {
    "id": 202,
    "title": "Beta Film",
    "year": 2022,
    "status": "released",
    "minimumAvailability": "released",
    "isAvailable": True,
    "inCinemas": "2022-06-01T00:00:00Z",
    "physicalRelease": "2022-09-01T00:00:00Z",
    "releaseDate": "2022-09-01T00:00:00Z",
    "digitalRelease": None,
}

_COMMAND_RESP: dict[str, Any] = {"id": 1, "name": "Search"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instance(
    *,
    instance_id: int = 1,
    itype: InstanceType = InstanceType.sonarr,
    url: str = SONARR_URL,
    batch_size: int = 10,
    hourly_cap: int = 20,
    cooldown_days: int = 7,
    post_release_grace_hrs: int = 24,
    cutoff_enabled: bool = False,
    cutoff_batch_size: int = 5,
    cutoff_hourly_cap: int = 10,
    cutoff_cooldown_days: int = 21,
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode,
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album,
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book,
    whisparr_search_mode: WhisparrSearchMode = WhisparrSearchMode.episode,
) -> Instance:
    return Instance(
        id=instance_id,
        name="Golden Test",
        type=itype,
        url=url,
        api_key="test-api-key",
        enabled=True,
        batch_size=batch_size,
        sleep_interval_mins=15,
        hourly_cap=hourly_cap,
        cooldown_days=cooldown_days,
        post_release_grace_hrs=post_release_grace_hrs,
        queue_limit=0,
        cutoff_enabled=cutoff_enabled,
        cutoff_batch_size=cutoff_batch_size,
        cutoff_cooldown_days=cutoff_cooldown_days,
        cutoff_hourly_cap=cutoff_hourly_cap,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        sonarr_search_mode=sonarr_search_mode,
        lidarr_search_mode=lidarr_search_mode,
        readarr_search_mode=readarr_search_mode,
        whisparr_search_mode=whisparr_search_mode,
    )


async def _get_log_rows() -> list[dict[str, Any]]:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM search_log ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed FK-required rows so cooldowns and search_log can reference them."""
    from houndarr.crypto import encrypt

    encrypted = encrypt("test-api-key", MASTER_KEY)
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url, encrypted_api_key) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", SONARR_URL, encrypted),
                (2, "Radarr Test", "radarr", RADARR_URL, encrypted),
                (3, "Lidarr Test", "lidarr", LIDARR_URL, encrypted),
                (4, "Readarr Test", "readarr", READARR_URL, encrypted),
                (5, "Whisparr Test", "whisparr", WHISPARR_URL, encrypted),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# G1: Sonarr missing + cutoff (episode mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_golden_sonarr_episode_missing_and_cutoff(seeded_instances: None) -> None:
    """Sonarr episode-mode cycle with both missing and cutoff passes.

    Cooldown is shared across search_kind (the ``cooldowns`` table has no
    ``search_kind`` column), so items searched in the missing pass are
    on cooldown during the cutoff pass.  The cutoff pass uses a different
    episode (301) that was not in the missing response.

    Expected sequence:
      1. searched - ep 101, missing
      2. searched - ep 102, missing
      3. skipped  - ep 101, cutoff  (on cutoff cooldown from missing pass)
      4. searched - ep 301, cutoff
    """
    missing_page = {"records": [_EP_S01E01, _EP_S02E01]}
    cutoff_ep = {
        **_EP_S01E01,
        "id": 301,
        "title": "Cutoff Only",
        "seasonNumber": 3,
        "episodeNumber": 1,
    }
    cutoff_page = {"records": [_EP_S01E01, cutoff_ep]}

    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_page)
    )
    respx.get(f"{SONARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=cutoff_page)
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(
        batch_size=10,
        cutoff_enabled=True,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
    )
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g1")

    assert count == 3
    rows = await _get_log_rows()
    assert len(rows) == 4

    # Row 0: missing pass - episode 101
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 101
    assert rows[0]["item_type"] == "episode"
    assert rows[0]["item_label"] == "My Show - S01E01 - Pilot"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g1"
    assert rows[0]["cycle_trigger"] == "scheduled"
    assert rows[0]["reason"] is None

    # Row 1: missing pass - episode 102
    assert rows[1]["action"] == "searched"
    assert rows[1]["item_id"] == 102
    assert rows[1]["item_type"] == "episode"
    assert rows[1]["item_label"] == "My Show - S02E01 - Premiere"
    assert rows[1]["search_kind"] == "missing"
    assert rows[1]["cycle_id"] == "golden-g1"
    assert rows[1]["cycle_trigger"] == "scheduled"

    # Row 2: cutoff pass - episode 101 skipped (cooldown from missing pass)
    assert rows[2]["action"] == "skipped"
    assert rows[2]["item_id"] == 101
    assert rows[2]["item_type"] == "episode"
    assert rows[2]["search_kind"] == "cutoff"
    assert rows[2]["reason"] == "on cutoff cooldown (21d)"
    assert rows[2]["cycle_id"] == "golden-g1"

    # Row 3: cutoff pass - episode 301 searched
    assert rows[3]["action"] == "searched"
    assert rows[3]["item_id"] == 301
    assert rows[3]["item_type"] == "episode"
    assert rows[3]["item_label"] == "My Show - S03E01 - Cutoff Only"
    assert rows[3]["search_kind"] == "cutoff"
    assert rows[3]["cycle_id"] == "golden-g1"


# ---------------------------------------------------------------------------
# G2: Sonarr season-context mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_golden_sonarr_season_context(seeded_instances: None) -> None:
    """Season-context mode deduplicates by (series, season).

    Input: 3 episodes - 2 in S01, 1 in S02 of series 55.
    Expected sequence:
      1. searched - synthetic S01 ID, season-context label
      2. searched - synthetic S02 ID, season-context label
    Episode 102 (S01E02) is silently deduped by group_key, no log row.
    """
    from houndarr.engine.adapters.sonarr import _season_item_id

    missing_records = {
        "records": [
            {**_EP_S01E01, "id": 101, "seasonNumber": 1, "episodeNumber": 1},
            {**_EP_S01E01, "id": 102, "seasonNumber": 1, "episodeNumber": 2, "title": "Second"},
            {**_EP_S02E01, "id": 103, "seasonNumber": 2, "episodeNumber": 1},
        ]
    }
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        side_effect=[
            httpx.Response(200, json=missing_records),
            httpx.Response(200, json={"records": []}),
        ]
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(sonarr_search_mode=SonarrSearchMode.season_context)
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g2")

    assert count == 2
    rows = await _get_log_rows()
    assert len(rows) == 2

    # Row 0: season 1
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == _season_item_id(55, 1)
    assert rows[0]["item_type"] == "episode"
    assert rows[0]["item_label"] == "My Show - S01 (season-context)"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g2"

    # Row 1: season 2
    assert rows[1]["action"] == "searched"
    assert rows[1]["item_id"] == _season_item_id(55, 2)
    assert rows[1]["item_type"] == "episode"
    assert rows[1]["item_label"] == "My Show - S02 (season-context)"
    assert rows[1]["search_kind"] == "missing"
    assert rows[1]["cycle_id"] == "golden-g2"


# ---------------------------------------------------------------------------
# G3: Radarr missing + cutoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_golden_radarr_missing_and_cutoff(seeded_instances: None) -> None:
    """Radarr cycle with both missing and cutoff passes.

    Cooldown is shared across search_kind, so movies 201/202 searched in the
    missing pass are on cooldown during the cutoff pass.  The cutoff response
    includes a fresh movie (203) alongside the already-searched 201.

    Expected sequence:
      1. searched - movie 201, missing
      2. searched - movie 202, missing
      3. skipped  - movie 201, cutoff  (on cutoff cooldown from missing pass)
      4. searched - movie 203, cutoff
    """
    movie_c: dict[str, Any] = {
        "id": 203,
        "title": "Gamma Picture",
        "year": 2024,
        "status": "released",
        "minimumAvailability": "released",
        "isAvailable": True,
        "inCinemas": "2024-01-01T00:00:00Z",
        "physicalRelease": "2024-04-01T00:00:00Z",
        "releaseDate": "2024-04-01T00:00:00Z",
        "digitalRelease": None,
    }

    missing_page = {"records": [_MOVIE_A, _MOVIE_B]}
    cutoff_page = {"records": [_MOVIE_A, movie_c]}

    respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_page)
    )
    respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=cutoff_page)
    )
    respx.post(f"{RADARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(
        instance_id=2,
        itype=InstanceType.radarr,
        url=RADARR_URL,
        batch_size=10,
        cutoff_enabled=True,
        cutoff_batch_size=10,
        cutoff_cooldown_days=21,
    )
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g3")

    assert count == 3
    rows = await _get_log_rows()
    assert len(rows) == 4

    # Missing pass - movie A
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 201
    assert rows[0]["item_type"] == "movie"
    assert rows[0]["item_label"] == "Alpha Movie (2023)"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g3"
    assert rows[0]["cycle_trigger"] == "scheduled"

    # Missing pass - movie B
    assert rows[1]["action"] == "searched"
    assert rows[1]["item_id"] == 202
    assert rows[1]["item_type"] == "movie"
    assert rows[1]["item_label"] == "Beta Film (2022)"
    assert rows[1]["search_kind"] == "missing"

    # Cutoff pass - movie A skipped (cooldown from missing pass)
    assert rows[2]["action"] == "skipped"
    assert rows[2]["item_id"] == 201
    assert rows[2]["item_type"] == "movie"
    assert rows[2]["search_kind"] == "cutoff"
    assert rows[2]["reason"] == "on cutoff cooldown (21d)"
    assert rows[2]["cycle_id"] == "golden-g3"

    # Cutoff pass - movie C searched
    assert rows[3]["action"] == "searched"
    assert rows[3]["item_id"] == 203
    assert rows[3]["item_type"] == "movie"
    assert rows[3]["item_label"] == "Gamma Picture (2024)"
    assert rows[3]["search_kind"] == "cutoff"


# ---------------------------------------------------------------------------
# G4: Mixed eligibility (unreleased + cooldown + hourly cap)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@respx.mock
async def test_golden_mixed_eligibility(seeded_instances: None) -> None:
    """A single Sonarr missing pass with multiple skip reasons.

    The pipeline evaluates checks in this order: unreleased → hourly cap →
    cooldown.  When hourly cap fires it sets ``stop_pass = True`` and breaks
    the inner loop, so no further items are evaluated.  To demonstrate all
    four outcomes in one pass we use ``hourly_cap=2`` and order the items so
    that the cooldown check is reached before the cap is exhausted.

    Setup:
      - ep 101: eligible (searched - uses 1 of 2 cap slots)
      - ep 102: unreleased (future air date → skipped before cap check)
      - ep 103: on cooldown (passes unreleased + cap, hits cooldown)
      - ep 104: eligible (searched - uses 2 of 2 cap slots)
      - ep 105: hourly cap reached (cap=2 exhausted → skipped, stop_pass)

    Expected sequence:
      1. searched - ep 101
      2. skipped  - ep 102, "not yet released"
      3. skipped  - ep 103, "on cooldown (7d)"
      4. searched - ep 104
      5. skipped  - ep 105, "hourly cap reached (2)"
    """
    from houndarr.services.cooldown import record_search

    # Seed cooldown for ep 103.
    await record_search(1, 103, "episode")

    episodes = [
        {**_EP_S01E01, "id": 101, "airDateUtc": "2023-01-10T00:00:00Z"},
        {
            **_EP_S01E01,
            "id": 102,
            "airDateUtc": "2999-01-01T00:00:00Z",
            "title": "Future",
            "episodeNumber": 2,
        },
        {
            **_EP_S01E01,
            "id": 103,
            "airDateUtc": "2023-03-01T00:00:00Z",
            "title": "Cooldown",
            "episodeNumber": 3,
        },
        {
            **_EP_S01E01,
            "id": 104,
            "airDateUtc": "2023-04-01T00:00:00Z",
            "title": "Fourth",
            "episodeNumber": 4,
        },
        {
            **_EP_S01E01,
            "id": 105,
            "airDateUtc": "2023-05-01T00:00:00Z",
            "title": "Capped",
            "episodeNumber": 5,
        },
    ]
    respx.get(f"{SONARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json={"records": episodes})
    )
    respx.post(f"{SONARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(
        hourly_cap=2,
        cooldown_days=7,
        post_release_grace_hrs=24,
    )
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g4")

    assert count == 2
    rows = await _get_log_rows()
    assert len(rows) == 5

    # Row 0: searched
    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 101
    assert rows[0]["item_type"] == "episode"
    assert rows[0]["item_label"] == "My Show - S01E01 - Pilot"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g4"
    assert rows[0]["cycle_trigger"] == "scheduled"
    assert rows[0]["reason"] is None

    # Row 1: skipped - unreleased
    assert rows[1]["action"] == "skipped"
    assert rows[1]["item_id"] == 102
    assert rows[1]["item_type"] == "episode"
    assert rows[1]["item_label"] == "My Show - S01E02 - Future"
    assert rows[1]["search_kind"] == "missing"
    assert rows[1]["reason"] == "not yet released"
    assert rows[1]["cycle_id"] == "golden-g4"

    # Row 2: skipped - cooldown
    assert rows[2]["action"] == "skipped"
    assert rows[2]["item_id"] == 103
    assert rows[2]["item_type"] == "episode"
    assert rows[2]["item_label"] == "My Show - S01E03 - Cooldown"
    assert rows[2]["search_kind"] == "missing"
    assert rows[2]["reason"] == "on cooldown (7d)"
    assert rows[2]["cycle_id"] == "golden-g4"

    # Row 3: searched - fourth episode (uses second cap slot)
    assert rows[3]["action"] == "searched"
    assert rows[3]["item_id"] == 104
    assert rows[3]["item_type"] == "episode"
    assert rows[3]["item_label"] == "My Show - S01E04 - Fourth"
    assert rows[3]["search_kind"] == "missing"
    assert rows[3]["cycle_id"] == "golden-g4"
    assert rows[3]["reason"] is None

    # Row 4: skipped - hourly cap
    assert rows[4]["action"] == "skipped"
    assert rows[4]["item_id"] == 105
    assert rows[4]["item_type"] == "episode"
    assert rows[4]["item_label"] == "My Show - S01E05 - Capped"
    assert rows[4]["search_kind"] == "missing"
    assert rows[4]["reason"] == "hourly cap reached (2)"
    assert rows[4]["cycle_id"] == "golden-g4"


# ---------------------------------------------------------------------------
# G5: Lidarr missing + cutoff (album mode)
# ---------------------------------------------------------------------------

_ALBUM_A: dict[str, Any] = {
    "id": 301,
    "artistId": 50,
    "title": "Greatest Hits",
    "releaseDate": "2023-03-15T00:00:00Z",
    "artist": {"id": 50, "artistName": "Test Artist"},
}
_ALBUM_B: dict[str, Any] = {
    "id": 302,
    "artistId": 50,
    "title": "Live Sessions",
    "releaseDate": "2023-06-01T00:00:00Z",
    "artist": {"id": 50, "artistName": "Test Artist"},
}


@pytest.mark.asyncio()
@respx.mock
async def test_golden_lidarr_album_missing_and_cutoff(seeded_instances: None) -> None:
    """Lidarr album-mode cycle with both missing and cutoff passes.

    Expected sequence:
      1. searched - album 301, missing
      2. searched - album 302, missing
      3. skipped  - album 301, cutoff (on cutoff cooldown from missing pass)
      4. searched - album 303, cutoff
    """
    album_c: dict[str, Any] = {
        "id": 303,
        "artistId": 50,
        "title": "Rarities",
        "releaseDate": "2024-01-01T00:00:00Z",
        "artist": {"id": 50, "artistName": "Test Artist"},
    }

    missing_page = {"records": [_ALBUM_A, _ALBUM_B]}
    cutoff_page = {"records": [_ALBUM_A, album_c]}

    respx.get(f"{LIDARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_page)
    )
    respx.get(f"{LIDARR_URL}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=cutoff_page)
    )
    respx.post(f"{LIDARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(
        instance_id=3,
        itype=InstanceType.lidarr,
        url=LIDARR_URL,
        batch_size=10,
        cutoff_enabled=True,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
    )
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g5")

    assert count == 3
    rows = await _get_log_rows()
    assert len(rows) == 4

    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 301
    assert rows[0]["item_type"] == "album"
    assert rows[0]["item_label"] == "Test Artist - Greatest Hits"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g5"

    assert rows[1]["action"] == "searched"
    assert rows[1]["item_id"] == 302
    assert rows[1]["item_type"] == "album"
    assert rows[1]["item_label"] == "Test Artist - Live Sessions"
    assert rows[1]["search_kind"] == "missing"

    assert rows[2]["action"] == "skipped"
    assert rows[2]["item_id"] == 301
    assert rows[2]["item_type"] == "album"
    assert rows[2]["search_kind"] == "cutoff"
    assert rows[2]["reason"] == "on cutoff cooldown (21d)"

    assert rows[3]["action"] == "searched"
    assert rows[3]["item_id"] == 303
    assert rows[3]["item_type"] == "album"
    assert rows[3]["item_label"] == "Test Artist - Rarities"
    assert rows[3]["search_kind"] == "cutoff"


# ---------------------------------------------------------------------------
# G6: Readarr missing + cutoff (book mode)
# ---------------------------------------------------------------------------

_BOOK_A: dict[str, Any] = {
    "id": 401,
    "authorId": 60,
    "title": "Foundation",
    "releaseDate": "2023-01-01T00:00:00Z",
    "author": {"id": 60, "authorName": "Asimov"},
}
_BOOK_B: dict[str, Any] = {
    "id": 402,
    "authorId": 60,
    "title": "Foundation and Empire",
    "releaseDate": "2023-06-01T00:00:00Z",
    "author": {"id": 60, "authorName": "Asimov"},
}


@pytest.mark.asyncio()
@respx.mock
async def test_golden_readarr_book_missing_and_cutoff(seeded_instances: None) -> None:
    """Readarr book-mode cycle with both missing and cutoff passes.

    Expected sequence:
      1. searched - book 401, missing
      2. searched - book 402, missing
      3. skipped  - book 401, cutoff (on cutoff cooldown from missing pass)
      4. searched - book 403, cutoff
    """
    book_c: dict[str, Any] = {
        "id": 403,
        "authorId": 60,
        "title": "Second Foundation",
        "releaseDate": "2024-01-01T00:00:00Z",
        "author": {"id": 60, "authorName": "Asimov"},
    }

    missing_page = {"records": [_BOOK_A, _BOOK_B]}
    cutoff_page = {"records": [_BOOK_A, book_c]}

    respx.get(f"{READARR_URL}/api/v1/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_page)
    )
    respx.get(f"{READARR_URL}/api/v1/wanted/cutoff").mock(
        return_value=httpx.Response(200, json=cutoff_page)
    )
    respx.post(f"{READARR_URL}/api/v1/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(
        instance_id=4,
        itype=InstanceType.readarr,
        url=READARR_URL,
        batch_size=10,
        cutoff_enabled=True,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
    )
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g6")

    assert count == 3
    rows = await _get_log_rows()
    assert len(rows) == 4

    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 401
    assert rows[0]["item_type"] == "book"
    assert rows[0]["item_label"] == "Asimov - Foundation"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g6"

    assert rows[1]["action"] == "searched"
    assert rows[1]["item_id"] == 402
    assert rows[1]["item_type"] == "book"
    assert rows[1]["item_label"] == "Asimov - Foundation and Empire"

    assert rows[2]["action"] == "skipped"
    assert rows[2]["item_id"] == 401
    assert rows[2]["search_kind"] == "cutoff"
    assert rows[2]["reason"] == "on cutoff cooldown (21d)"

    assert rows[3]["action"] == "searched"
    assert rows[3]["item_id"] == 403
    assert rows[3]["item_type"] == "book"
    assert rows[3]["item_label"] == "Asimov - Second Foundation"
    assert rows[3]["search_kind"] == "cutoff"


# ---------------------------------------------------------------------------
# G7: Whisparr missing (episode mode)
# ---------------------------------------------------------------------------

_WHISPARR_EP_A: dict[str, Any] = {
    "id": 501,
    "seriesId": 70,
    "title": "Scene A",
    "seasonNumber": 1,
    "absoluteEpisodeNumber": 1,
    "releaseDate": {"year": 2023, "month": 3, "day": 1},
    "series": {"id": 70, "title": "Whisparr Show"},
}
_WHISPARR_EP_B: dict[str, Any] = {
    "id": 502,
    "seriesId": 70,
    "title": "Scene B",
    "seasonNumber": 2,
    "absoluteEpisodeNumber": 2,
    "releaseDate": {"year": 2023, "month": 6, "day": 1},
    "series": {"id": 70, "title": "Whisparr Show"},
}


@pytest.mark.asyncio()
@respx.mock
async def test_golden_whisparr_episode_missing(seeded_instances: None) -> None:
    """Whisparr episode-mode missing pass with two episodes.

    Expected sequence:
      1. searched - ep 501, missing, item_type=whisparr_episode
      2. searched - ep 502, missing, item_type=whisparr_episode
    """
    missing_page = {"records": [_WHISPARR_EP_A, _WHISPARR_EP_B]}

    respx.get(f"{WHISPARR_URL}/api/v3/wanted/missing").mock(
        return_value=httpx.Response(200, json=missing_page)
    )
    respx.post(f"{WHISPARR_URL}/api/v3/command").mock(
        return_value=httpx.Response(201, json=_COMMAND_RESP)
    )

    instance = _make_instance(
        instance_id=5,
        itype=InstanceType.whisparr,
        url=WHISPARR_URL,
    )
    count = await run_instance_search(instance, MASTER_KEY, cycle_id="golden-g7")

    assert count == 2
    rows = await _get_log_rows()
    assert len(rows) == 2

    assert rows[0]["action"] == "searched"
    assert rows[0]["item_id"] == 501
    assert rows[0]["item_type"] == "whisparr_episode"
    assert rows[0]["item_label"] == "Whisparr Show - S01 - Scene A"
    assert rows[0]["search_kind"] == "missing"
    assert rows[0]["cycle_id"] == "golden-g7"

    assert rows[1]["action"] == "searched"
    assert rows[1]["item_id"] == 502
    assert rows[1]["item_type"] == "whisparr_episode"
    assert rows[1]["item_label"] == "Whisparr Show - S02 - Scene B"
    assert rows[1]["search_kind"] == "missing"

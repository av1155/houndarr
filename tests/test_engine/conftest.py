"""Shared fixtures and helpers for engine test files."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from houndarr.database import get_db
from houndarr.engine.candidates import ItemType
from houndarr.services.cooldown import _reset_skip_log_cache
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
    WhisparrV2SearchMode,
)


@pytest.fixture(autouse=True)
def _reset_skip_log_sentinel() -> Iterator[None]:
    """Clear the in-memory cooldown-skip sentinel between engine tests.

    Without this, a test that triggers ``should_log_skip`` leaves cache
    entries that suppress skip writes in the next test, producing
    order-dependent test failures.
    """
    _reset_skip_log_cache()
    yield
    _reset_skip_log_cache()


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
LIDARR_URL = "http://lidarr:8686"
READARR_URL = "http://readarr:8787"
WHISPARR_V2_URL = "http://whisparr:6969"
WHISPARR_V3_URL = "http://whisparr-v3:6970"
MASTER_KEY: bytes = Fernet.generate_key()

_EPISODE_RECORD: dict[str, Any] = {
    "id": 101,
    "seriesId": 55,
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
    "status": "released",
    "minimumAvailability": "released",
    "isAvailable": True,
    "inCinemas": "2023-01-01T00:00:00Z",
    "physicalRelease": "2023-02-01T00:00:00Z",
    "releaseDate": "2023-02-01T00:00:00Z",
    "digitalRelease": None,
}

_ALBUM_RECORD: dict[str, Any] = {
    "id": 301,
    "artistId": 50,
    "title": "Greatest Hits",
    "releaseDate": "2023-03-15T00:00:00Z",
    "artist": {"id": 50, "artistName": "Test Artist"},
}

_BOOK_RECORD: dict[str, Any] = {
    "id": 401,
    "authorId": 60,
    "title": "Test Book",
    "releaseDate": "2023-06-01T00:00:00Z",
    "author": {"id": 60, "authorName": "Test Author"},
}

_WHISPARR_V2_EPISODE_RECORD: dict[str, Any] = {
    "id": 501,
    "seriesId": 70,
    "title": "Scene Title",
    "seasonNumber": 1,
    "absoluteEpisodeNumber": 5,
    "releaseDate": {"year": 2023, "month": 9, "day": 1},
    "series": {"id": 70, "title": "My Whisparr v2 Show"},
}

_MISSING_SONARR: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_EPISODE_RECORD],
}
_MISSING_RADARR: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_MOVIE_RECORD],
}
_MISSING_LIDARR: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_ALBUM_RECORD],
}
_MISSING_READARR: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_BOOK_RECORD],
}
_MISSING_WHISPARR_V2: dict[str, Any] = {
    "page": 1,
    "pageSize": 10,
    "totalRecords": 1,
    "records": [_WHISPARR_V2_EPISODE_RECORD],
}
_COMMAND_RESP: dict[str, Any] = {"id": 1, "name": "EpisodeSearch"}
_FUTURE_AIR_DATE = "2999-01-01T00:00:00Z"

# URL map for easy per-type lookups
URL_FOR_TYPE: dict[InstanceType, str] = {
    InstanceType.sonarr: SONARR_URL,
    InstanceType.radarr: RADARR_URL,
    InstanceType.lidarr: LIDARR_URL,
    InstanceType.readarr: READARR_URL,
    InstanceType.whisparr_v2: WHISPARR_V2_URL,
}


# ---------------------------------------------------------------------------
# Instance factory
# ---------------------------------------------------------------------------


def make_instance(
    *,
    instance_id: int = 1,
    itype: InstanceType = InstanceType.sonarr,
    url: str | None = None,
    batch_size: int = 10,
    hourly_cap: int = 20,
    cooldown_days: int = 7,
    post_release_grace_hrs: int = 24,
    queue_limit: int = 0,
    enabled: bool = True,
    cutoff_enabled: bool = False,
    cutoff_batch_size: int = 5,
    cutoff_cooldown_days: int = 21,
    cutoff_hourly_cap: int = 1,
    sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode,
    lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album,
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book,
    whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode.episode,
    upgrade_enabled: bool = False,
    upgrade_batch_size: int = 1,
    upgrade_cooldown_days: int = 90,
    upgrade_hourly_cap: int = 1,
    upgrade_sonarr_search_mode: SonarrSearchMode = SonarrSearchMode.episode,
    upgrade_lidarr_search_mode: LidarrSearchMode = LidarrSearchMode.album,
    upgrade_readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book,
    upgrade_whisparr_v2_search_mode: WhisparrV2SearchMode = WhisparrV2SearchMode.episode,
    upgrade_item_offset: int = 0,
    upgrade_series_offset: int = 0,
    missing_page_offset: int = 1,
    cutoff_page_offset: int = 1,
    allowed_time_window: str = "",
    search_order: SearchOrder = SearchOrder.chronological,
) -> Instance:
    """Build an Instance with sensible defaults for testing."""
    resolved_url = url or URL_FOR_TYPE.get(itype, SONARR_URL)
    return Instance(
        core=InstanceCore(
            id=instance_id,
            name="Test Instance",
            type=itype,
            url=resolved_url,
            api_key="test-api-key",
            enabled=enabled,
        ),
        missing=MissingPolicy(
            batch_size=batch_size,
            sleep_interval_mins=15,
            hourly_cap=hourly_cap,
            cooldown_days=cooldown_days,
            post_release_grace_hrs=post_release_grace_hrs,
            queue_limit=queue_limit,
            sonarr_search_mode=sonarr_search_mode,
            lidarr_search_mode=lidarr_search_mode,
            readarr_search_mode=readarr_search_mode,
            whisparr_v2_search_mode=whisparr_v2_search_mode,
        ),
        cutoff=CutoffPolicy(
            cutoff_enabled=cutoff_enabled,
            cutoff_batch_size=cutoff_batch_size,
            cutoff_cooldown_days=cutoff_cooldown_days,
            cutoff_hourly_cap=cutoff_hourly_cap,
        ),
        upgrade=UpgradePolicy(
            upgrade_enabled=upgrade_enabled,
            upgrade_batch_size=upgrade_batch_size,
            upgrade_cooldown_days=upgrade_cooldown_days,
            upgrade_hourly_cap=upgrade_hourly_cap,
            upgrade_sonarr_search_mode=upgrade_sonarr_search_mode,
            upgrade_lidarr_search_mode=upgrade_lidarr_search_mode,
            upgrade_readarr_search_mode=upgrade_readarr_search_mode,
            upgrade_whisparr_v2_search_mode=upgrade_whisparr_v2_search_mode,
            upgrade_item_offset=upgrade_item_offset,
            upgrade_series_offset=upgrade_series_offset,
        ),
        schedule=SchedulePolicy(
            allowed_time_window=allowed_time_window,
            search_order=search_order,
            missing_page_offset=missing_page_offset,
            cutoff_page_offset=cutoff_page_offset,
        ),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def seeded_instances(db: None) -> AsyncGenerator[None, None]:
    """Seed FK-required rows into instances so search_log/cooldowns can reference them."""
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
                (5, "Whisparr Test", "whisparr_v2", WHISPARR_V2_URL, encrypted),
                (6, "Whisparr V3 Test", "whisparr_v3", WHISPARR_V3_URL, encrypted),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def get_log_rows() -> list[dict[str, Any]]:
    """Fetch all search_log rows ordered by id."""
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM search_log ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def insert_search_log_row(
    *,
    instance_id: int,
    item_id: int,
    item_type: str,
    search_kind: str,
    action: str,
    reason: str | None = None,
    cycle_id: str | None = None,
    cycle_trigger: str | None = None,
) -> None:
    """Insert a raw search_log row for test setup."""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO search_log
                (instance_id, item_id, item_type, search_kind, action, reason,
                 cycle_id, cycle_trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (instance_id, item_id, item_type, search_kind, action, reason, cycle_id, cycle_trigger),
        )
        await conn.commit()


async def seed_release_timing_retry(
    *,
    instance_id: int,
    item_id: int,
    item_type: ItemType,
    reason: str = "not yet released",
) -> None:
    """Set up an item on cooldown with a release-timing skip reason."""
    from houndarr.services.cooldown import record_search

    await record_search(instance_id, item_id, item_type)
    await insert_search_log_row(
        instance_id=instance_id,
        item_id=item_id,
        item_type=item_type,
        search_kind="missing",
        action="skipped",
        reason=reason,
    )

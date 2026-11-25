"""Warn when an *arr reports a different sort than the client asked for.

Allowlisted builds (Sonarr 4.0.9.2421+, Whisparr v2 2.2.0+, Radarr) swap an
unlisted ``sortKey`` for their own default and say so only in the response
envelope, so a key that stops being honoured reads as a healthy page in
the wrong order.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import respx

import houndarr.clients.base as base_module
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.sonarr import SonarrClient

_SONARR = "http://sonarr:8989"


@pytest.fixture(autouse=True)
def _fresh_warn_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "_sort_echo_warned", set())


def _envelope(**sort: Any) -> dict[str, Any]:
    return {"page": 1, "pageSize": 10, "totalRecords": 0, "records": [], **sort}


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "houndarr.clients.base" and record.levelno == logging.WARNING
    ]


async def _fetch_cutoff(url: str = _SONARR) -> None:
    async with SonarrClient(url=url, api_key="k") as client:
        await client.get_cutoff_unmet(page=1, page_size=10)


@pytest.mark.asyncio()
@respx.mock
async def test_a_swapped_key_warns_once_per_url_and_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    swapped = _envelope(sortKey="series.sortTitle", sortDirection="ascending")
    respx.get(f"{_SONARR}/api/v3/wanted/cutoff").respond(json=swapped)
    respx.get(f"{_SONARR}/api/v3/wanted/missing").respond(json=swapped)
    respx.get("http://sonarr-4k:8989/api/v3/wanted/cutoff").respond(json=swapped)

    with caplog.at_level(logging.WARNING, logger="houndarr.clients.base"):
        await _fetch_cutoff()
        await _fetch_cutoff()
        async with SonarrClient(url=_SONARR, api_key="k") as client:
            await client.get_missing(page=1, page_size=10)
            await client.get_wanted_total("missing")
        await _fetch_cutoff("http://sonarr-4k:8989")

    messages = _warnings(caplog)
    assert len(messages) == 3
    assert messages[0] == (
        "Wanted list http://sonarr:8989/api/v3/wanted/cutoff came back sorted by "
        "series.sortTitle ascending instead of airDateUtc ascending; "
        "Chronological order is not oldest-first"
    )
    assert "/api/v3/wanted/missing" in messages[1]
    assert messages[2].startswith("Wanted list http://sonarr-4k:8989/")


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "sort",
    [
        {"sortKey": "episodes.airDateUtc", "sortDirection": "ascending"},
        {"sortKey": "Episodes.AirDateUtc", "sortDirection": "Ascending"},
        {"sortKey": "airDateUtc", "sortDirection": "ascending"},
        {},
        {"sortKey": "airDateUtc", "sortDirection": 1},
        {"sortKey": "airDateUtc"},
    ],
    ids=["qualified", "case", "bare", "no-echo", "int-direction", "no-direction"],
)
@respx.mock
async def test_the_requested_sort_reported_back_does_not_warn(
    caplog: pytest.LogCaptureFixture, sort: dict[str, Any]
) -> None:
    respx.get(f"{_SONARR}/api/v3/wanted/cutoff").respond(json=_envelope(**sort))
    with caplog.at_level(logging.WARNING, logger="houndarr.clients.base"):
        await _fetch_cutoff()
    assert _warnings(caplog) == []


@pytest.mark.asyncio()
@respx.mock
async def test_a_flipped_direction_warns(caplog: pytest.LogCaptureFixture) -> None:
    respx.get("http://lidarr:8686/api/v1/wanted/cutoff").respond(
        json=_envelope(sortKey="releaseDate", sortDirection="descending")
    )
    with caplog.at_level(logging.WARNING, logger="houndarr.clients.base"):
        async with LidarrClient(url="http://lidarr:8686", api_key="k") as client:
            await client.get_cutoff_unmet(page=1, page_size=10)
    assert _warnings(caplog) == [
        "Wanted list http://lidarr:8686/api/v1/wanted/cutoff came back sorted by "
        "releaseDate descending instead of releaseDate ascending; "
        "Chronological order is not oldest-first"
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_an_odd_echo_never_fails_the_page() -> None:
    record = {"id": 7, "seriesId": 1, "title": "Pilot", "seasonNumber": 1, "episodeNumber": 1}
    respx.get(f"{_SONARR}/api/v3/wanted/cutoff").respond(
        json={**_envelope(sortKey=5, sortDirection=[1.5]), "records": [record]}
    )
    async with SonarrClient(url=_SONARR, api_key="k") as client:
        items = await client.get_cutoff_unmet(page=1, page_size=10)
    assert [item.episode_id for item in items] == [7]


@pytest.mark.asyncio()
@respx.mock
async def test_the_warning_does_not_interrupt_the_fetch() -> None:
    record = {"id": 7, "seriesId": 1, "title": "Pilot", "seasonNumber": 1, "episodeNumber": 1}
    respx.get(f"{_SONARR}/api/v3/wanted/cutoff").respond(
        json={**_envelope(sortKey="episodes.id", sortDirection="descending"), "records": [record]}
    )
    async with SonarrClient(url=_SONARR, api_key="k") as client:
        items = await client.get_cutoff_unmet(page=1, page_size=10)
    assert [item.episode_id for item in items] == [7]

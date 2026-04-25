"""Tests for GET /api/logs, GET /api/logs/partial, and GET /logs."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from houndarr.clients.base import ArrClient
from houndarr.database import get_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUTH_LOCATIONS = {"/setup", "/login", "http://testserver/setup", "http://testserver/login"}

_VALID_FORM = {
    "name": "My Sonarr",
    "type": "sonarr",
    "url": "http://sonarr:8989",
    "api_key": "test-api-key",
    "connection_verified": "true",
}


@pytest.fixture(autouse=True)
def _mock_connection_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _always_ok(self: ArrClient) -> dict[str, Any] | None:
        name = type(self).__name__.replace("Client", "")
        return {"appName": name, "version": "4.0.0"}

    monkeypatch.setattr(ArrClient, "ping", _always_ok)


def _login(client: TestClient) -> None:
    client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    client.post("/login", data={"username": "admin", "password": "ValidPass1!"})


async def _insert_extra_logs(count: int, *, start_index: int = 0) -> None:
    """Insert many deterministic rows for pagination behavior tests."""
    rows: list[tuple[object, ...]] = []
    for index in range(start_index, start_index + count):
        hour = (index // 3600) % 24
        minute = (index // 60) % 60
        second = index % 60
        rows.append(
            (
                1,
                10000 + index,
                "episode",
                "missing",
                f"cycle-bulk-{index // 5}",
                "scheduled",
                f"Bulk row {index}",
                "skipped",
                "bulk",
                None,
                f"2024-01-02T{hour:02d}:{minute:02d}:{second:02d}.000Z",
            )
        )

    async with get_db() as conn:
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


@pytest_asyncio.fixture()
async def seeded_log(db: None) -> AsyncGenerator[None, None]:  # type: ignore[misc]
    """Seed search_log with rows across two instances for filter/pagination tests."""
    async with get_db() as conn:
        # Seed two instances so FK constraint on search_log is satisfied
        await conn.executemany(
            "INSERT INTO instances (id, name, type, url) VALUES (?, ?, ?, ?)",
            [
                (1, "Sonarr Test", "sonarr", "http://sonarr:8989"),
                (2, "Radarr Test", "radarr", "http://radarr:7878"),
            ],
        )
        # Seed a variety of log rows
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
            [
                (
                    1,
                    101,
                    "episode",
                    "missing",
                    "cycle-a",
                    "scheduled",
                    "My Show - S01E01 - Pilot",
                    "searched",
                    None,
                    None,
                    "2024-01-01T12:00:00.000Z",
                ),
                (
                    1,
                    102,
                    "episode",
                    "cutoff",
                    "cycle-a",
                    "scheduled",
                    "My Show - S01E02 - Next",
                    "skipped",
                    "on cooldown (7d)",
                    None,
                    "2024-01-01T12:01:00.000Z",
                ),
                (
                    2,
                    201,
                    "movie",
                    "missing",
                    "cycle-b",
                    "run_now",
                    "My Movie (2023)",
                    "searched",
                    None,
                    None,
                    "2024-01-01T12:02:00.000Z",
                ),
                (
                    2,
                    202,
                    "movie",
                    "missing",
                    "cycle-b",
                    "run_now",
                    "Another Movie (2024)",
                    "error",
                    None,
                    "connection refused",
                    "2024-01-01T12:03:00.000Z",
                ),
                (
                    1,
                    103,
                    "episode",
                    "missing",
                    "cycle-c",
                    "scheduled",
                    "My Show - S01E03 - Fill",
                    "skipped",
                    "already queued",
                    None,
                    "2024-01-01T12:00:30.000Z",
                ),
                (
                    None,
                    None,
                    None,
                    None,
                    None,
                    "system",
                    None,
                    "info",
                    None,
                    "Supervisor started 2 task(s)",
                    "2024-01-01T11:59:00.000Z",
                ),
            ],
        )
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------


def test_logs_api_redirects_unauthenticated(app: TestClient) -> None:
    """Unauthenticated request to /api/logs should redirect to login."""
    resp = app.get("/api/logs", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_logs_page_redirects_unauthenticated(app: TestClient) -> None:
    """Unauthenticated request to /logs should redirect to login."""
    resp = app.get("/logs", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


def test_logs_partial_redirects_unauthenticated(app: TestClient) -> None:
    """Unauthenticated request to /api/logs/partial should redirect to login."""
    resp = app.get("/api/logs/partial", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] in _AUTH_LOCATIONS


# ---------------------------------------------------------------------------
# GET /api/logs - empty state
# ---------------------------------------------------------------------------


def test_logs_empty_when_no_entries(app: TestClient) -> None:
    """Returns an empty list when search_log has no rows."""
    _login(app)
    resp = app.get("/api/logs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/logs - with seeded data (uses async DB fixture + sync app)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_logs_returns_all_rows(seeded_log: None, async_client: object) -> None:
    """Returns all seeded rows with correct fields when no filter is applied."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    # Setup + login via the async client
    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 6

    # Newest first (by timestamp DESC)
    actions = [r["action"] for r in data]
    assert actions[0] == "error"  # 12:03
    assert actions[-1] == "info"  # 11:59
    assert data[0]["item_label"] == "Another Movie (2024)"
    assert data[0]["search_kind"] == "missing"
    assert data[0]["cycle_id"] == "cycle-b"
    assert data[0]["cycle_trigger"] == "run_now"
    assert data[0]["cycle_progress"] == "progress"


@pytest.mark.asyncio()
async def test_logs_filter_by_instance_id(seeded_log: None, async_client: object) -> None:
    """Filtering by instance_id returns only that instance's rows."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?instance_id=1&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    for row in data:
        assert row["instance_id"] == 1


@pytest.mark.asyncio()
async def test_logs_empty_instance_id_treated_as_all(
    seeded_log: None, async_client: object
) -> None:
    """HTMX-style empty instance_id should mean no filter, not a 422."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?instance_id=&limit=200")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


@pytest.mark.asyncio()
async def test_logs_filter_by_action(seeded_log: None, async_client: object) -> None:
    """Filtering by action returns only rows with that action."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?action=searched&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for row in data:
        assert row["action"] == "searched"


@pytest.mark.asyncio()
async def test_logs_filter_by_search_kind(seeded_log: None, async_client: object) -> None:
    """Filtering by search_kind returns only rows with that kind."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?search_kind=cutoff&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["search_kind"] == "cutoff"


@pytest.mark.asyncio()
async def test_logs_filter_by_cycle_trigger(seeded_log: None, async_client: object) -> None:
    """Filtering by cycle_trigger returns only rows with that trigger."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?cycle_trigger=run_now&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(row["cycle_trigger"] == "run_now" for row in data)


@pytest.mark.asyncio()
async def test_logs_hide_system_rows_filter(seeded_log: None, async_client: object) -> None:
    """hide_system=true should remove system lifecycle rows from results."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?hide_system=true&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5
    assert all(row["cycle_trigger"] != "system" for row in data)


@pytest.mark.asyncio()
async def test_logs_filters_compose_with_existing_filters(
    seeded_log: None, async_client: object
) -> None:
    """Existing and new filters should compose deterministically."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get(
        "/api/logs?instance_id=1&action=skipped&search_kind=cutoff&cycle_trigger=scheduled&hide_system=true&limit=200"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["instance_id"] == 1
    assert row["action"] == "skipped"
    assert row["search_kind"] == "cutoff"
    assert row["cycle_trigger"] == "scheduled"


@pytest.mark.asyncio()
async def test_logs_empty_action_treated_as_all(seeded_log: None, async_client: object) -> None:
    """HTMX-style empty action should mean no filter, not action='' filter."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?action=&limit=200")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


@pytest.mark.asyncio()
async def test_logs_system_rows_render_as_system_label(
    seeded_log: None, async_client: object
) -> None:
    """Rows with NULL instance_id should be labeled 'System', not 'Deleted'."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?action=info&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["instance_id"] is None
    assert data[0]["instance_name"] == "System"
    assert data[0]["cycle_id"] is None
    assert data[0]["cycle_trigger"] == "system"


@pytest.mark.asyncio()
async def test_logs_limit_restricts_rows(seeded_log: None, async_client: object) -> None:
    """The limit param caps the number of rows returned."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio()
async def test_logs_before_cursor_paginates(seeded_log: None, async_client: object) -> None:
    """The 'before' cursor returns only rows older than the given timestamp."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    # All rows older than 12:02 -> should be 12:01, 12:00:30, 12:00, 11:59.
    resp = await async_client.get("/api/logs?before=2024-01-01T12:02:00.000Z&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4
    for row in data:
        assert row["timestamp"] < "2024-01-01T12:02:00.000Z"


# ---------------------------------------------------------------------------
# GET /logs page
# ---------------------------------------------------------------------------


def test_logs_page_renders(app: TestClient) -> None:
    """The /logs page renders 200 OK with the expected HTML structure."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b'data-page-key="logs"' in resp.content
    assert b"log-filter-form" in resp.content
    assert b"log-tbody" in resp.content
    assert b"Media" in resp.content
    assert b"Timestamp (Local)" in resp.content
    assert b"Kind" in resp.content
    assert b"Trigger" in resp.content
    assert b"Cycle" in resp.content
    assert b"Cycle outcome" in resp.content
    assert b"Hide system rows" in resp.content
    assert b'id="summary-total-rows"' in resp.content
    assert b'id="filter-hide-system"' in resp.content
    assert b"checked" in resp.content
    assert b'<option value="500">500</option>' in resp.content
    assert b'<option value="1000">1000</option>' in resp.content
    assert b'<option value="5000">All</option>' in resp.content
    # Split-button copy dropdown must be present.
    assert b"Copy as TSV" in resp.content
    assert b"Copy as Markdown" in resp.content
    assert b"Copy as JSON" in resp.content
    assert b"Copy as plain text" in resp.content
    assert b'data-copy-main="true"' in resp.content
    assert b'data-copy-chevron="true"' in resp.content
    assert b'data-copy-format="tsv"' in resp.content
    assert b'data-copy-format="markdown"' in resp.content
    assert b'data-copy-format="json"' in resp.content
    assert b'data-copy-format="text"' in resp.content
    # Mobile and desktop groups both present.
    assert b'id="copy-dropdown-group-mobile"' in resp.content
    assert b'id="copy-dropdown-group-desktop"' in resp.content
    # Old single-button IDs must not appear.
    assert b'id="copy-visible-logs-btn"' not in resp.content
    assert b'id="copy-visible-logs-btn-mobile"' not in resp.content
    assert b"Copy visible rows" not in resp.content


def test_logs_page_hx_request_returns_content_fragment(app: TestClient) -> None:
    """HX-Request for /logs should return shell content fragment only."""
    _login(app)
    resp = app.get("/logs", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b'data-page-key="logs"' in resp.content
    assert b'id="log-filter-form"' in resp.content
    assert b"<html" not in resp.content


def test_logs_page_hx_request_includes_copy_dropdown(app: TestClient) -> None:
    """HX-partial /logs response must include full split-button dropdown structure."""
    _login(app)
    resp = app.get("/logs", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    # Both placements present in the fragment.
    assert b'id="copy-dropdown-group-mobile"' in resp.content
    assert b'id="copy-dropdown-group-desktop"' in resp.content
    # All four format menu items present.
    assert b'data-copy-format="tsv"' in resp.content
    assert b'data-copy-format="markdown"' in resp.content
    assert b'data-copy-format="json"' in resp.content
    assert b'data-copy-format="text"' in resp.content
    # Main button and chevron attributes present.
    assert b'data-copy-main="true"' in resp.content
    assert b'data-copy-chevron="true"' in resp.content
    # Menu role attributes present (accessibility).
    assert b'role="menu"' in resp.content
    assert b'role="menuitem"' in resp.content
    # No old single-button markup.
    assert b'id="copy-visible-logs-btn"' not in resp.content
    assert b"Copy visible rows" not in resp.content


def test_logs_page_copy_dropdown_menu_items_text(app: TestClient) -> None:
    """The dropdown must contain the correct human-readable label for each format."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b"Copy as TSV" in resp.content
    assert b"Copy as Markdown" in resp.content
    assert b"Copy as JSON" in resp.content
    assert b"Copy as plain text" in resp.content


def test_logs_page_copy_dropdown_aria_attributes(app: TestClient) -> None:
    """The chevron button must have aria-haspopup and aria-expanded attributes."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b'aria-haspopup="menu"' in resp.content
    assert b'aria-expanded="false"' in resp.content
    assert b'aria-label="Open copy format menu"' in resp.content


# ---------------------------------------------------------------------------
# GET /api/logs/partial - HTMX partial
# ---------------------------------------------------------------------------


def test_logs_partial_empty(app: TestClient) -> None:
    """The HTMX partial returns the empty-state row when no logs exist."""
    _login(app)
    resp = app.get("/api/logs/partial")
    assert resp.status_code == 200
    assert b"No log entries found" in resp.content


@pytest.mark.asyncio()
async def test_logs_partial_returns_rows(seeded_log: None, async_client: object) -> None:
    """The HTMX partial contains <tr> elements when rows exist."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=200")
    assert resp.status_code == 200
    content = resp.text
    assert "<tr" in content
    assert 'data-cycle-group="cycle-b"' in content
    # Should contain action badges
    assert "searched" in content or "skipped" in content
    assert "My Show - S01E01 - Pilot" in content
    assert "run_now" in content
    assert "skips only" in content
    assert "unknown" in content


@pytest.mark.asyncio()
async def test_logs_partial_empty_instance_id_treated_as_all(
    seeded_log: None, async_client: object
) -> None:
    """Partial endpoint should accept empty instance_id from the filter form."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?instance_id=&limit=200")
    assert resp.status_code == 200
    assert "<tr" in resp.text


@pytest.mark.asyncio()
async def test_logs_partial_hide_system_rows_excludes_system_entries(
    seeded_log: None, async_client: object
) -> None:
    """Partial endpoint should hide system rows when hide_system=true."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?hide_system=true&limit=200")
    assert resp.status_code == 200
    assert "Supervisor started" not in resp.text


@pytest.mark.asyncio()
async def test_logs_partial_pagination_uses_append_swap(
    seeded_log: None, async_client: object
) -> None:
    """Load-older control should append older rows instead of replacing current rows."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=2")
    assert resp.status_code == 200
    assert 'hx-target="#pagination-row"' in resp.text
    assert 'hx-swap="outerHTML"' in resp.text


@pytest.mark.asyncio()
async def test_logs_partial_load_more_caps_chunk_size_for_high_limits(
    seeded_log: None, async_client: object
) -> None:
    """High selected row counts should paginate in bounded chunks."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await _insert_extra_logs(620)
    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=500&hide_system=true")
    assert resp.status_code == 200
    assert "limit=100" in resp.text
    assert 'hx-target="#pagination-row"' in resp.text


@pytest.mark.asyncio()
async def test_logs_partial_load_more_preserves_small_limits(
    seeded_log: None, async_client: object
) -> None:
    """Smaller limits should keep their original pagination chunk size."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await _insert_extra_logs(80, start_index=1000)
    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=50&hide_system=true")
    assert resp.status_code == 200
    assert "limit=50" in resp.text


@pytest.mark.asyncio()
async def test_logs_partial_fallback_media_when_item_label_missing(
    seeded_log: None, async_client: object
) -> None:
    """Rows without item_label should fall back to item type + ID in Media column."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    async with get_db() as conn:
        await conn.execute("UPDATE search_log SET item_label = NULL WHERE item_id = 102")
        await conn.commit()

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=200")
    assert resp.status_code == 200
    assert "Episode 102" in resp.text


@pytest.mark.asyncio()
async def test_logs_partial_cycle_group_headers_include_cycle_context(
    seeded_log: None, async_client: object
) -> None:
    """Cycle group rows should include trigger and per-cycle action totals."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?limit=200")
    assert resp.status_code == 200
    assert "Cycle cycle-b" in resp.text
    assert "trigger run_now" in resp.text
    assert "searched 1" in resp.text
    assert "skipped 1" in resp.text


# ---------------------------------------------------------------------------
# Row-limit extensions: 1000 and All (5000)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_logs_limit_1000_accepted(seeded_log: None, async_client: object) -> None:
    """limit=1000 must be accepted and return available rows."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=1000")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


@pytest.mark.asyncio()
async def test_logs_limit_all_accepted(seeded_log: None, async_client: object) -> None:
    """limit=5000 (the 'All' sentinel) must be accepted and return available rows."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=5000")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


@pytest.mark.asyncio()
async def test_logs_limit_above_max_rejected(seeded_log: None, async_client: object) -> None:
    """limit above _LOG_LIMIT_MAX (5000) must be rejected with 422."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?limit=5001")
    assert resp.status_code == 422


@pytest.mark.asyncio()
async def test_logs_partial_returns_html_422_on_bad_filter(
    seeded_log: None, async_client: object
) -> None:
    """``/api/logs/partial`` must return an HTML ``<tr>`` error row on
    validation failure, not FastAPI's default JSON body.

    Rationale: the partial is swapped into ``#log-tbody`` via HTMX.
    With the ``422 -> swap`` config override in ``base.html``, a JSON
    response would render as raw ``{"detail": ...}`` inside the
    ``<tbody>``.  The endpoint shapes the error as a ``<tr>`` instead.
    """
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?search_kind=totally_invalid")
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("text/html"), resp.headers
    body = resp.text
    assert "<tr" in body and 'colspan="10"' in body, body
    assert "Invalid filter value" in body, body
    # The specific detail string is surfaced so operators can see what failed.
    assert "search_kind" in body, body


def test_logs_page_renders_all_limit_options(app: TestClient) -> None:
    """The /logs page must include the 1000 and All (5000) options in the Rows selector."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b'<option value="1000">1000</option>' in resp.content
    assert b'<option value="5000">All</option>' in resp.content


def test_logs_summary_no_legacy_rows_in_html(app: TestClient) -> None:
    """The /logs summary bar must not contain a 'legacy rows' label."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b"legacy rows" not in resp.content


def test_logs_summary_no_unknown_cycles_in_html(app: TestClient) -> None:
    """The /logs summary bar must not contain an 'unknown cycles' label."""
    _login(app)
    resp = app.get("/logs")
    assert resp.status_code == 200
    assert b"unknown cycles" not in resp.content

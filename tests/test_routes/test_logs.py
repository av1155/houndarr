"""Tests for GET /api/logs, GET /api/logs/partial, and GET /logs."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from houndarr.clients._wire_models import SystemStatus
from houndarr.clients.base import ArrClient
from houndarr.database import get_db
from tests.conftest import csrf_headers

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
    async def _always_ok(self: ArrClient) -> SystemStatus | None:
        name = type(self).__name__.replace("Client", "")
        return SystemStatus(app_name=name, version="4.0.0")

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
async def test_logs_filter_by_multiple_instance_ids(seeded_log: None, async_client: object) -> None:
    """Repeated instance_id params narrow the feed to the selected instances.

    Exercises the multi-select contract added for the dashboard error
    banner: ``/logs?instance_id=1&instance_id=2`` must return rows whose
    ``instance_id`` is in ``{1, 2}`` and nothing else.  The seeded
    fixture owns two instances (ids 1 and 2), so asserting that both
    appear rules out accidental single-value fallback.
    """
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?instance_id=1&instance_id=2&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    returned_ids = {row["instance_id"] for row in data}
    # The seed also carries a system row whose instance_id IS NULL;
    # the IN-clause filter must exclude it so we only see rows for the
    # two instances explicitly asked for.
    assert returned_ids == {1, 2}
    for row in data:
        assert row["instance_id"] in {1, 2}


@pytest.mark.asyncio()
async def test_logs_filter_rejects_non_integer_instance_id(
    seeded_log: None, async_client: object
) -> None:
    """A non-integer instance_id value returns a 422 (multi-select preserved)."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?instance_id=1&instance_id=abc")
    assert resp.status_code == 422


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
async def test_logs_hide_skipped_filter(seeded_log: None, async_client: object) -> None:
    """hide_skipped=true should remove action='skipped' rows from results."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs?hide_skipped=true&limit=200")
    assert resp.status_code == 200
    data = resp.json()
    assert all(row["action"] != "skipped" for row in data)


@pytest.mark.asyncio()
async def test_logs_partial_hide_skipped_excludes_skipped_rows(
    seeded_log: None, async_client: object
) -> None:
    """Partial endpoint honours hide_skipped=true and round-trips it in pagination."""
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    resp = await async_client.get("/api/logs/partial?hide_skipped=true&limit=200")
    assert resp.status_code == 200
    content = resp.content.decode()
    # data-action is stamped on every row in both the legacy table and
    # the redesigned cycle feed, so the filter's effect is visible as
    # the absence of the skipped value regardless of template shape.
    assert 'data-action="skipped"' not in content


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
    assert b'hx-history="false"' in resp.content
    assert b'id="log-filter-form"' in resp.content
    assert b'id="log-feed"' in resp.content
    assert b'id="live-indicator"' in resp.content
    assert b"Kind" in resp.content
    assert b"Trigger" in resp.content
    # Noise switches (hide_system default on, hide_skipped default off).
    assert b'id="filter-hide-system"' in resp.content
    assert b'id="filter-hide-skipped"' in resp.content
    assert b"Hide system" in resp.content
    assert b"Hide skipped" in resp.content
    assert b"checked" in resp.content
    # Rows selector: full option set preserved from the legacy page.
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
    # Legacy summary bar and table shell must not leak back in.
    assert b"log-tbody" not in resp.content
    assert b'id="summary-total-rows"' not in resp.content


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


def test_logs_partial_empty_when_db_has_no_rows(app: TestClient) -> None:
    """A truly empty search_log renders the quiet "no entries yet" branch.

    Distinguishes a fresh install (or post Clear logs) from a filter that
    happens to exclude every row: the route probes
    ``search_log_has_any_row`` when the filtered query came back empty
    and passes ``log_db_empty=True`` so the partial picks the
    ``empty--quiet`` Station panel instead of the filter-mismatch copy.
    """
    _login(app)
    resp = app.get("/api/logs/partial")
    assert resp.status_code == 200
    assert b"empty--quiet" in resp.content
    assert b"No log entries yet" in resp.content
    assert b"No entries match those filters" not in resp.content


@pytest.mark.asyncio()
async def test_logs_partial_empty_when_only_system_rows_exist(
    db: None, async_client: object
) -> None:
    """A search_log holding only the supervisor-startup row reads as empty.

    Pin: the supervisor writes a ``cycle_trigger='system'``,
    ``instance_id IS NULL``, ``action='info'`` row on every boot.
    Without this regression, a naive "any row" probe would fire on the
    very first app start and the new empty-state branch would never
    surface for users.  The probe must mirror ``query_logs``'s
    ``hide_system`` predicate so any DB whose only rows are system
    lifecycle reads as empty.
    """
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    async with get_db() as conn:
        await conn.execute(
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
            VALUES (NULL, NULL, NULL, NULL, NULL, 'system', NULL, 'info', NULL,
                    'Supervisor started 0 task(s)', '2024-01-01T00:00:00.000Z')
            """
        )
        await conn.commit()

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    # ``hide_system=true`` matches the page-route default and the
    # partial query the live UI sends after a filter change.  Under
    # that filter the only row in the DB drops out, the partial
    # returns an empty result, and the probe is consulted: it must
    # ignore the system row and report the table as empty.
    resp = await async_client.get("/api/logs/partial?hide_system=true")
    assert resp.status_code == 200
    body = resp.content
    assert b"empty--quiet" in body
    assert b"No log entries yet" in body
    assert b"No entries match those filters" not in body


@pytest.mark.asyncio()
async def test_logs_partial_empty_when_filters_exclude_all_rows(
    seeded_log: None, async_client: object
) -> None:
    """When rows exist but the filter excludes every one, keep the original copy.

    Pin: ``log_db_empty`` must be False whenever the underlying table has
    any row, even if the filtered query returns zero, so the user sees
    the actionable "clear a filter" hint instead of the fresh-install copy.
    """
    from httpx import AsyncClient

    assert isinstance(async_client, AsyncClient)

    await async_client.post(
        "/setup",
        data={"username": "admin", "password": "ValidPass1!", "password_confirm": "ValidPass1!"},
    )
    await async_client.post("/login", data={"username": "admin", "password": "ValidPass1!"})

    # search_kind=upgrade does not match any seeded row.
    resp = await async_client.get("/api/logs/partial?search_kind=upgrade")
    assert resp.status_code == 200
    body = resp.content
    assert b"No entries match those filters" in body
    assert b"empty--quiet" not in body
    assert b"No log entries yet" not in body


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
    assert 'class="cycle' in content
    assert 'data-cycle-id="cycle-b"' in content
    # Cycle cards expose the trigger as a data attribute and render it
    # as the lowercased label in the meta line.
    assert 'data-cycle-trigger="run_now"' in content
    assert "run now" in content
    # Entry chips + titles.
    assert 'class="entry' in content
    assert "My Show - S01E01 - Pilot" in content


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
    assert 'class="cycle' in resp.text


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
    # Cycle identity + trigger live on data attributes; counts render
    # as outcome pills whose inner span carries the number.  The " N"
    # label text sits outside the span so the exact byte sequence is
    # `outcome-pill__n">1</span> searched`.
    assert 'data-cycle-id="cycle-b"' in resp.text
    assert 'data-cycle-trigger="run_now"' in resp.text
    assert "outcome-pill--searched" in resp.text
    assert "outcome-pill--error" in resp.text
    assert 'outcome-pill__n">1</span>' in resp.text


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
    """``/api/logs/partial`` must return a feed-shaped error card on
    validation failure, not FastAPI's default JSON body.

    Rationale: the partial is swapped into ``#log-feed`` (a
    ``<section>``) via HTMX.  With the ``422 -> swap`` config
    override in ``base.html``, a JSON response would render as raw
    ``{"detail": ...}`` inside the section.  The endpoint shapes
    the error as a ``<div class="empty empty--error">`` card to
    match the feed's visual language.
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
    assert 'class="empty' in body and "empty--error" in body, body
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


# ---------------------------------------------------------------------------
# PR 4: /logs query-param pre-filtering (dashboard error-banner / pill links)
# ---------------------------------------------------------------------------


def test_logs_page_accepts_instance_id_query_param(app: TestClient) -> None:
    """Following /logs?instance_id=1 keeps the filter selected on the page."""
    _login(app)
    app.post("/settings/instances", data=_VALID_FORM, headers=csrf_headers(app))

    resp = app.get("/logs?instance_id=1&action=error")
    assert resp.status_code == 200
    body = resp.content
    # The instance multi-select dropdown pre-checks the box whose value
    # matches the deep link's instance_id.  The checkbox sits inside a
    # <label> so the `value="1" ... checked` substring is enough to
    # pin the pre-selection without coupling to layout whitespace.
    assert b'value="1"' in body
    assert b"checked" in body
    # The action filter dropdown should surface the requested action.
    assert b'value="error" selected' in body


def test_logs_page_unknown_filter_falls_back_to_unfiltered(app: TestClient) -> None:
    """Malformed query strings should not 422 the page; fall back cleanly."""
    _login(app)
    resp = app.get("/logs?instance_id=not-a-number")
    assert resp.status_code == 200

"""Answer a mocked client's ``get_wanted_page`` from in-memory wanted lists."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from houndarr.clients.base import WantedPage


def serve_wanted(
    client: AsyncMock, *, missing: list[Any] | None = None, cutoff: list[Any] | None = None
) -> None:
    """Serve each list in pages with a steady total, as a healthy *arr would."""
    lists = {"missing": missing or [], "cutoff": cutoff or []}

    async def page(kind: str, *, page: int, page_size: int) -> WantedPage[Any]:
        items = lists[kind]
        start = (page - 1) * page_size
        chunk = items[start : start + page_size]
        return WantedPage(
            items=chunk,
            ids=tuple(range(start, start + len(chunk))),
            total_records=len(items),
            total_counts_rows=False,
        )

    client.get_wanted_page.side_effect = page

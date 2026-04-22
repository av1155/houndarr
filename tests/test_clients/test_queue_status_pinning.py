"""Pin the QueueStatus wire contract and per-API-version path dispatch.

Track A.7 of the refactor plan.  The existing
``tests/test_clients/test_client_edge_cases.py`` covers happy-path
queue-status fetches per app.  This module pins the Pydantic wire-model
contract (``totalCount`` -> ``total_count`` alias, extra-fields ignored,
missing-field rejection) so any future *arr upgrade that adds new
fields to the ``QueueStatusResource`` payload does not break us.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import ValidationError

from houndarr.clients._wire_models import QueueStatus
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient

pytestmark = pytest.mark.pinning


# ---------------------------------------------------------------------------
# QueueStatus Pydantic contract
# ---------------------------------------------------------------------------


class TestQueueStatusWireContract:
    """Pin the alias-based parsing behaviour and field tolerance."""

    def test_camel_case_alias_populates_snake_case(self) -> None:
        """``totalCount`` (camelCase from the wire) populates ``total_count``."""
        status = QueueStatus.model_validate({"totalCount": 42})
        assert status.total_count == 42

    def test_snake_case_also_accepted(self) -> None:
        """``populate_by_name=True`` means snake_case input is accepted too."""
        status = QueueStatus.model_validate({"total_count": 7})
        assert status.total_count == 7

    def test_extra_fields_ignored(self) -> None:
        """Unknown fields from a future *arr version are silently dropped."""
        status = QueueStatus.model_validate(
            {
                "totalCount": 3,
                "unknownCount": 1,  # real *arr emits this in later versions
                "errors": False,
                "brand_new_future_field": "ignored",
            }
        )
        assert status.total_count == 3

    def test_missing_total_count_raises(self) -> None:
        """Without ``totalCount`` (and no snake alias) model_validate fails."""
        with pytest.raises(ValidationError):
            QueueStatus.model_validate({"errors": False})

    def test_total_count_zero_accepted(self) -> None:
        """Zero-item queue is a legitimate state, not missing."""
        status = QueueStatus.model_validate({"totalCount": 0})
        assert status.total_count == 0

    def test_large_total_count_accepted(self) -> None:
        """Very large totals do not saturate or wrap (pinning: int, not bounded)."""
        status = QueueStatus.model_validate({"totalCount": 1_000_000})
        assert status.total_count == 1_000_000


# ---------------------------------------------------------------------------
# Per-API-version path dispatch (end-to-end smoke pinning)
# ---------------------------------------------------------------------------


class TestQueueStatusPathDispatch:
    """Pin that v3 and v1 apps hit the correct path AND return a parsed model."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sonarr_uses_v3_path_and_returns_parsed(self) -> None:
        route = respx.get("http://sonarr:8989/api/v3/queue/status").mock(
            return_value=httpx.Response(200, json={"totalCount": 11}),
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="k") as client:
            status = await client.get_queue_status()
        assert route.called
        assert status.total_count == 11

    @pytest.mark.asyncio()
    @respx.mock
    async def test_lidarr_uses_v1_path_and_returns_parsed(self) -> None:
        route = respx.get("http://lidarr:8686/api/v1/queue/status").mock(
            return_value=httpx.Response(200, json={"totalCount": 5}),
        )
        async with LidarrClient(url="http://lidarr:8686", api_key="k") as client:
            status = await client.get_queue_status()
        assert route.called
        assert status.total_count == 5

    @pytest.mark.asyncio()
    @respx.mock
    async def test_readarr_uses_v1_path_and_returns_parsed(self) -> None:
        route = respx.get("http://readarr:8787/api/v1/queue/status").mock(
            return_value=httpx.Response(200, json={"totalCount": 2}),
        )
        async with ReadarrClient(url="http://readarr:8787", api_key="k") as client:
            status = await client.get_queue_status()
        assert route.called
        assert status.total_count == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_validation_error_bubbles_up(self) -> None:
        """When the wire payload is malformed, get_queue_status raises.

        Pinning: unlike ``ping()`` which swallows every failure to None,
        ``get_queue_status`` is contractually allowed to raise both
        ``httpx.HTTPError`` and ``pydantic.ValidationError``.
        """
        respx.get("http://sonarr:8989/api/v3/queue/status").mock(
            return_value=httpx.Response(200, json={"errors": False}),  # no totalCount
        )
        async with SonarrClient(url="http://sonarr:8989", api_key="k") as client:
            with pytest.raises(ValidationError):
                await client.get_queue_status()

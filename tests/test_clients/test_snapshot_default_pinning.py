"""Pin the default get_instance_snapshot contract across subclass overrides.

Track A.6 of the refactor plan.  The default implementation on
``ArrClient`` sums ``get_wanted_total('missing')`` and
``get_wanted_total('cutoff')`` to produce ``monitored_total`` and
defers ``unreleased_count`` to ``_count_unreleased_default`` (currently
``0`` for /wanted-based clients).

``WhisparrV3Client`` overrides ``get_instance_snapshot`` entirely
because it has no ``/wanted`` endpoint; we pin that the override
takes precedence and the base-class default path is NOT hit when the
subclass redefines the method.

These tests are orthogonal to ``test_base_pinning.py``: they focus on
call-count invariants (``get_wanted_total`` must run once per kind, not
more) and on the subclass-override dispatch rule.
"""

from __future__ import annotations

from typing import Any

import pytest

from houndarr.clients.base import ArrClient, InstanceSnapshot, WantedKind

pytestmark = pytest.mark.pinning


class _CallCountingStub(ArrClient):
    """Records every call to get_wanted_total + _count_unreleased_default."""

    wanted_calls: list[WantedKind]
    unreleased_calls: int

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        missing_total: int = 0,
        cutoff_total: int = 0,
    ) -> None:
        super().__init__(url=url, api_key=api_key)
        self.wanted_calls = []
        self.unreleased_calls = 0
        self._missing_total = missing_total
        self._cutoff_total = cutoff_total

    async def get_missing(self, *, page: int = 1, page_size: int = 10) -> list[Any]:
        return []

    async def get_cutoff_unmet(self, *, page: int = 1, page_size: int = 10) -> list[Any]:
        return []

    async def search(self, item_id: int) -> None:
        return None

    async def get_wanted_total(self, kind: WantedKind) -> int:
        self.wanted_calls.append(kind)
        return self._missing_total if kind == "missing" else self._cutoff_total

    async def _count_unreleased_default(self) -> int:
        self.unreleased_calls += 1
        return await super()._count_unreleased_default()


class _OverridingSubclass(_CallCountingStub):
    """Concrete subclass that overrides get_instance_snapshot entirely."""

    async def get_instance_snapshot(self) -> InstanceSnapshot:
        # Override: do NOT call the base-class helpers.  Subclass is
        # free to implement its own counting strategy (the Whisparr v3
        # client does exactly this).
        return InstanceSnapshot(monitored_total=999, unreleased_count=7)


# Default path call-count invariants


class TestDefaultSnapshotCallCount:
    """Pin that the default path makes exactly the helper calls it promises."""

    @pytest.mark.asyncio()
    async def test_calls_get_wanted_total_once_per_kind(self) -> None:
        """The default path hits get_wanted_total twice: once for missing, once for cutoff."""
        stub = _CallCountingStub(
            url="http://sonarr:8989",
            api_key="k",
            missing_total=5,
            cutoff_total=9,
        )
        try:
            await stub.get_instance_snapshot()
        finally:
            await stub.aclose()
        assert stub.wanted_calls == ["missing", "cutoff"]

    @pytest.mark.asyncio()
    async def test_calls_count_unreleased_default_exactly_once(self) -> None:
        """_count_unreleased_default fires exactly once per snapshot."""
        stub = _CallCountingStub(url="http://sonarr:8989", api_key="k")
        try:
            await stub.get_instance_snapshot()
        finally:
            await stub.aclose()
        assert stub.unreleased_calls == 1

    @pytest.mark.asyncio()
    async def test_monitored_total_is_arithmetic_sum(self) -> None:
        """monitored_total = missing_total + cutoff_total (no dedup / no cap)."""
        stub = _CallCountingStub(
            url="http://sonarr:8989",
            api_key="k",
            missing_total=100,
            cutoff_total=50,
        )
        try:
            snap = await stub.get_instance_snapshot()
        finally:
            await stub.aclose()
        assert snap.monitored_total == 150

    @pytest.mark.asyncio()
    async def test_zero_totals_produce_zero_snapshot(self) -> None:
        """Both totals at zero: monitored_total is 0, unreleased stays 0."""
        stub = _CallCountingStub(url="http://sonarr:8989", api_key="k")
        try:
            snap = await stub.get_instance_snapshot()
        finally:
            await stub.aclose()
        assert snap.monitored_total == 0
        assert snap.unreleased_count == 0


# Subclass override dispatch


class TestSnapshotSubclassOverride:
    """Pin that a subclass override displaces the base-class default path."""

    @pytest.mark.asyncio()
    async def test_override_bypasses_base_helpers(self) -> None:
        """When a subclass overrides get_instance_snapshot, the base-class
        default path does NOT run.  get_wanted_total must not be called."""
        stub = _OverridingSubclass(
            url="http://whisparr:6969",
            api_key="k",
            missing_total=42,  # should be ignored because override skips get_wanted_total
            cutoff_total=42,
        )
        try:
            snap = await stub.get_instance_snapshot()
        finally:
            await stub.aclose()
        assert snap.monitored_total == 999
        assert snap.unreleased_count == 7
        assert stub.wanted_calls == []
        assert stub.unreleased_calls == 0

    @pytest.mark.asyncio()
    async def test_override_returns_instance_snapshot_dataclass(self) -> None:
        """Override must return InstanceSnapshot (pinning: return-type contract)."""
        stub = _OverridingSubclass(url="http://whisparr:6969", api_key="k")
        try:
            snap = await stub.get_instance_snapshot()
        finally:
            await stub.aclose()
        assert isinstance(snap, InstanceSnapshot)

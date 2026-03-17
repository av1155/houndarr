"""Tests for the adapter registry."""

from __future__ import annotations

import pytest

from houndarr.engine.adapters import ADAPTERS, AppAdapter, get_adapter
from houndarr.services.instances import InstanceType


class TestAdapterRegistry:
    """Verify the ADAPTERS registry and get_adapter lookup."""

    def test_has_sonarr(self):
        adapter = ADAPTERS[InstanceType.sonarr]
        assert isinstance(adapter, AppAdapter)
        assert callable(adapter.adapt_missing)
        assert callable(adapter.adapt_cutoff)
        assert callable(adapter.dispatch_search)
        assert callable(adapter.make_client)

    def test_has_radarr(self):
        adapter = ADAPTERS[InstanceType.radarr]
        assert isinstance(adapter, AppAdapter)
        assert callable(adapter.adapt_missing)
        assert callable(adapter.adapt_cutoff)
        assert callable(adapter.dispatch_search)
        assert callable(adapter.make_client)

    def test_exactly_two_entries(self):
        assert len(ADAPTERS) == 2

    def test_get_adapter_sonarr(self):
        adapter = get_adapter(InstanceType.sonarr)
        assert adapter is ADAPTERS[InstanceType.sonarr]

    def test_get_adapter_radarr(self):
        adapter = get_adapter(InstanceType.radarr)
        assert adapter is ADAPTERS[InstanceType.radarr]

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(ValueError, match="No adapter registered"):
            get_adapter("unknown")  # type: ignore[arg-type]

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

    def test_has_lidarr(self):
        adapter = ADAPTERS[InstanceType.lidarr]
        assert isinstance(adapter, AppAdapter)
        assert callable(adapter.adapt_missing)
        assert callable(adapter.adapt_cutoff)
        assert callable(adapter.dispatch_search)
        assert callable(adapter.make_client)

    def test_has_readarr(self):
        adapter = ADAPTERS[InstanceType.readarr]
        assert isinstance(adapter, AppAdapter)
        assert callable(adapter.adapt_missing)
        assert callable(adapter.adapt_cutoff)
        assert callable(adapter.dispatch_search)
        assert callable(adapter.make_client)

    def test_has_whisparr_v2(self):
        adapter = ADAPTERS[InstanceType.whisparr_v2]
        assert isinstance(adapter, AppAdapter)
        assert callable(adapter.adapt_missing)
        assert callable(adapter.adapt_cutoff)
        assert callable(adapter.dispatch_search)
        assert callable(adapter.make_client)

    def test_exactly_six_entries(self):
        assert len(ADAPTERS) == 6

    def test_get_adapter_sonarr(self):
        adapter = get_adapter(InstanceType.sonarr)
        assert adapter is ADAPTERS[InstanceType.sonarr]

    def test_get_adapter_radarr(self):
        adapter = get_adapter(InstanceType.radarr)
        assert adapter is ADAPTERS[InstanceType.radarr]

    def test_get_adapter_lidarr(self):
        adapter = get_adapter(InstanceType.lidarr)
        assert adapter is ADAPTERS[InstanceType.lidarr]

    def test_get_adapter_readarr(self):
        adapter = get_adapter(InstanceType.readarr)
        assert adapter is ADAPTERS[InstanceType.readarr]

    def test_get_adapter_whisparr_v2(self):
        adapter = get_adapter(InstanceType.whisparr_v2)
        assert adapter is ADAPTERS[InstanceType.whisparr_v2]

    def test_get_adapter_whisparr_v3(self):
        adapter = get_adapter(InstanceType.whisparr_v3)
        assert adapter is ADAPTERS[InstanceType.whisparr_v3]

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(ValueError, match="No adapter registered"):
            get_adapter("unknown")  # type: ignore[arg-type]

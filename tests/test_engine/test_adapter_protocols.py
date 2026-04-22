"""Conformance tests for engine.adapters.protocols.AppAdapterProto."""

from __future__ import annotations

import pytest

from houndarr.engine.adapters import ADAPTERS
from houndarr.engine.adapters.protocols import AppAdapterProto
from houndarr.services.instances import InstanceType


class TestAppAdapterProto:
    @pytest.mark.parametrize(
        "instance_type",
        [
            InstanceType.radarr,
            InstanceType.sonarr,
            InstanceType.lidarr,
            InstanceType.readarr,
            InstanceType.whisparr_v2,
            InstanceType.whisparr_v3,
        ],
    )
    def test_every_registered_adapter_satisfies_proto(self, instance_type: InstanceType) -> None:
        adapter = ADAPTERS[instance_type]
        assert isinstance(adapter, AppAdapterProto)

    def test_plain_object_does_not_satisfy(self) -> None:
        assert not isinstance(object(), AppAdapterProto)

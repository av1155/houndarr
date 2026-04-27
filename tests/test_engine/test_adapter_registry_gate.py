"""Consolidated invariant: the client + adapter registry stays whole.

Per-module pinning and characterisation tests cover each adapter
and each *arr client in detail; this gate locks the structural
shape above them.

Locked invariants:

* :data:`~houndarr.engine.adapters.ADAPTERS` registers exactly six
  instance types and each value is a class instance that conforms
  to :class:`AppAdapterProto`.
* The five paginated clients (Sonarr / Radarr / Lidarr / Readarr /
  Whisparr v2) each declare the four ``_WANTED_*`` template hooks;
  Whisparr v3 leaves ``_WANTED_ENVELOPE`` as ``None`` because it
  has no upstream ``/wanted`` endpoint.
* Each per-app adapter module exposes a class whose name follows the
  ``XAdapter`` convention and structurally satisfies
  :class:`AppAdapterProto`.
* :func:`~houndarr.engine.adapters.get_adapter` returns the matching
  instance for every registered ``InstanceType`` and raises
  :class:`ValueError` for an unknown one.
"""

from __future__ import annotations

import pytest

from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from houndarr.engine.adapters import ADAPTERS, AppAdapter, get_adapter
from houndarr.engine.adapters.lidarr import LidarrAdapter
from houndarr.engine.adapters.protocols import AppAdapterProto
from houndarr.engine.adapters.radarr import RadarrAdapter
from houndarr.engine.adapters.readarr import ReadarrAdapter
from houndarr.engine.adapters.sonarr import SonarrAdapter
from houndarr.engine.adapters.whisparr_v2 import WhisparrV2Adapter
from houndarr.engine.adapters.whisparr_v3 import WhisparrV3Adapter
from houndarr.services.instances import InstanceType

pytestmark = pytest.mark.pinning


# Registry shape


class TestAdaptersRegistry:
    """Pin the ADAPTERS dict shape."""

    def test_count_is_six(self) -> None:
        """The plan's exit criterion: exactly six adapters registered."""
        assert len(ADAPTERS) == 6

    def test_keys_are_every_instance_type(self) -> None:
        """Every InstanceType has a matching adapter."""
        assert set(ADAPTERS.keys()) == set(InstanceType)

    def test_app_adapter_is_protocol_alias(self) -> None:
        """``AppAdapter`` is kept as an alias for ``AppAdapterProto``.

        Historical type hints and ``MagicMock(spec=...)`` sites
        still resolve to the structural Protocol through this alias.
        """
        assert AppAdapter is AppAdapterProto

    @pytest.mark.parametrize("instance_type", list(InstanceType))
    def test_each_value_is_proto_instance(self, instance_type: InstanceType) -> None:
        """Every registered adapter conforms to AppAdapterProto at runtime."""
        adapter = ADAPTERS[instance_type]
        assert isinstance(adapter, AppAdapterProto)


# Per-adapter class identity


class TestPerAdapterClasses:
    """Pin the XAdapter class shape and the registry binding."""

    @pytest.mark.parametrize(
        ("instance_type", "expected_cls"),
        [
            (InstanceType.radarr, RadarrAdapter),
            (InstanceType.sonarr, SonarrAdapter),
            (InstanceType.lidarr, LidarrAdapter),
            (InstanceType.readarr, ReadarrAdapter),
            (InstanceType.whisparr_v2, WhisparrV2Adapter),
            (InstanceType.whisparr_v3, WhisparrV3Adapter),
        ],
    )
    def test_registry_binds_expected_class(
        self, instance_type: InstanceType, expected_cls: type
    ) -> None:
        """Each instance type maps to an instance of its XAdapter class."""
        assert isinstance(ADAPTERS[instance_type], expected_cls)


# get_adapter dispatch


class TestGetAdapter:
    """Pin the get_adapter helper's contract."""

    @pytest.mark.parametrize("instance_type", list(InstanceType))
    def test_returns_registry_value(self, instance_type: InstanceType) -> None:
        """Lookup matches direct dict access."""
        assert get_adapter(instance_type) is ADAPTERS[instance_type]

    def test_unknown_type_raises_value_error(self) -> None:
        """Unknown lookups raise ValueError, not KeyError."""
        with pytest.raises(ValueError, match="No adapter registered"):
            get_adapter("not-an-instance-type")  # type: ignore[arg-type]


# Client template hook surface


class TestPaginatedClientHooks:
    """Pin the four _WANTED_* hooks on every paginated client."""

    @pytest.mark.parametrize(
        ("client_cls", "expected_base", "expected_sort", "expected_include"),
        [
            (SonarrClient, "/api/v3/wanted", "airDateUtc", "includeSeries"),
            (RadarrClient, "/api/v3/wanted", "inCinemas", None),
            (LidarrClient, "/api/v1/wanted", "releaseDate", "includeArtist"),
            (ReadarrClient, "/api/v1/wanted", "releaseDate", "includeAuthor"),
            (WhisparrV2Client, "/api/v3/wanted", "releaseDate", "includeSeries"),
        ],
    )
    def test_wanted_template_hooks(
        self,
        client_cls: type[ArrClient],
        expected_base: str,
        expected_sort: str,
        expected_include: str | None,
    ) -> None:
        """Each paginated client declares the four template hooks."""
        assert expected_base == client_cls._WANTED_BASE_PATH
        assert expected_sort == client_cls._WANTED_SORT_KEY
        assert expected_include == client_cls._WANTED_INCLUDE_PARAM
        assert client_cls._WANTED_ENVELOPE is not None


class TestWhisparrV3Outlier:
    """Pin the documented outlier shape on Whisparr v3."""

    def test_wanted_envelope_unset(self) -> None:
        """Whisparr v3 leaves _WANTED_ENVELOPE as None (no /wanted endpoint)."""
        assert WhisparrV3Client._WANTED_ENVELOPE is None

    def test_other_hooks_inherit_base_defaults(self) -> None:
        """Whisparr v3 hooks stay at the ABC defaults; nothing is wired."""
        assert WhisparrV3Client._WANTED_BASE_PATH == "/api/v3/wanted"
        assert WhisparrV3Client._WANTED_SORT_KEY == ""
        assert WhisparrV3Client._WANTED_INCLUDE_PARAM is None

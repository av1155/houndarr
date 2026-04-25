"""Pin the repository + factory Protocol declarations.

The declarations in :mod:`houndarr.protocols` must keep three
invariants for the rest of the codebase to rely on them:

* Every Protocol is ``@runtime_checkable``, so consumers can use
  ``isinstance(obj, Proto)`` as a conformance check.
* Every Protocol can be imported from ``houndarr.protocols``
  without pulling in any runtime-heavy modules (e.g. FastAPI).
* The module's ``__all__`` re-exports the five Protocol names.
"""

from __future__ import annotations

import pytest

from houndarr.protocols import (
    ClientFactory,
    CooldownRepository,
    InstanceRepository,
    SearchLogRepository,
    SettingsRepository,
)

pytestmark = pytest.mark.pinning


class TestRepositoryProtocolsDeclared:
    """Pin the repository + factory Protocol declarations."""

    @pytest.mark.parametrize(
        "proto",
        [
            ClientFactory,
            CooldownRepository,
            InstanceRepository,
            SearchLogRepository,
            SettingsRepository,
        ],
    )
    def test_each_protocol_is_runtime_checkable(self, proto: type) -> None:
        """``isinstance(x, proto)`` must not raise at import / call time.

        ``@runtime_checkable`` Protocols raise ``TypeError`` from
        ``isinstance`` only when the Protocol carries non-method
        members; all five declarations here use method-only syntax, so
        a no-op ``isinstance(object(), proto)`` should return ``False``
        cleanly without raising.
        """
        assert isinstance(object(), proto) is False

    def test_module_all_exports_every_protocol(self) -> None:
        """``houndarr.protocols.__all__`` must list every declared symbol.

        Covers the five repository + factory Protocols plus
        the :class:`SupervisorProto` and the :data:`RunNowStatus`
        Literal declared in :mod:`houndarr.protocols`.
        """
        import houndarr.protocols as module

        assert set(module.__all__) == {
            "ClientFactory",
            "CooldownRepository",
            "InstanceRepository",
            "RunNowStatus",
            "SearchLogRepository",
            "SettingsRepository",
            "SupervisorProto",
        }

    def test_empty_stub_is_not_accepted_as_instance_repository(self) -> None:
        """A bare ``object()`` fails the structural conformance check.

        Protects against a future accidental widening of the Protocol
        to zero methods (which would make every object conformant).
        """

        class Bare:
            pass

        assert not isinstance(Bare(), InstanceRepository)

    def test_minimal_stub_passes_settings_repository(self) -> None:
        """A stub implementing every SettingsRepository method conforms.

        Exercises the positive side of the runtime_checkable contract
        so concrete repositories can rely on the same check.
        """

        class Stub:
            async def get_setting(self, key: str) -> str | None:
                return None

            async def set_setting(self, key: str, value: str) -> None:
                return None

            async def delete_setting(self, key: str) -> None:
                return None

        assert isinstance(Stub(), SettingsRepository)

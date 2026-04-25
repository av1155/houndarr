"""Consolidated invariant: the :mod:`houndarr.errors` hierarchy stays whole.

Per-wrap-site pinning tests cover each ``except Exception``
replacement in detail; this gate snapshot locks the *shape* of the
exception hierarchy so a silent subclass drop or reparent fails
here.

Assertions:

* Every layer base (:class:`ClientError`, :class:`EngineError`,
  :class:`ServiceError`, :class:`RouteError`) inherits from the
  single root :class:`HoundarrError`.
* Every concrete subclass registered in this file's expected map
  inherits from its declared layer base.
* The total number of public error names exposed by the module
  matches the expected count; a future rename or drop surfaces
  here instead of silently disappearing.
* Each subclass constructs cleanly from a message string and
  preserves ``__cause__`` when chained via ``raise ... from``.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

import houndarr.errors as errors_module
from houndarr.errors import (
    AuthRejectedError,
    ClientError,
    ClientHTTPError,
    ClientRedirectError,
    ClientTransportError,
    ClientUnreachableError,
    ClientValidationError,
    CooldownStateError,
    CsrfValidationError,
    EngineDispatchError,
    EngineError,
    EngineOffsetPersistError,
    EnginePoolFetchError,
    EngineQueueProbeError,
    HoundarrError,
    InstanceValidationError,
    RouteError,
    ServiceError,
    TimeWindowSpecError,
)

pytestmark = pytest.mark.pinning


class HierarchyEntry(NamedTuple):
    """One concrete error class with the layer base it must inherit from."""

    concrete: type[HoundarrError]
    base: type[HoundarrError]


_HIERARCHY: tuple[HierarchyEntry, ...] = (
    # Client layer
    HierarchyEntry(ClientHTTPError, ClientError),
    HierarchyEntry(ClientRedirectError, ClientHTTPError),
    HierarchyEntry(ClientTransportError, ClientError),
    HierarchyEntry(ClientValidationError, ClientError),
    HierarchyEntry(ClientUnreachableError, ClientError),
    # Engine layer
    HierarchyEntry(EngineDispatchError, EngineError),
    HierarchyEntry(EnginePoolFetchError, EngineError),
    HierarchyEntry(EngineOffsetPersistError, EngineError),
    HierarchyEntry(EngineQueueProbeError, EngineError),
    # Service layer
    HierarchyEntry(InstanceValidationError, ServiceError),
    HierarchyEntry(CooldownStateError, ServiceError),
    HierarchyEntry(TimeWindowSpecError, ServiceError),
    # Route layer
    HierarchyEntry(CsrfValidationError, RouteError),
    HierarchyEntry(AuthRejectedError, RouteError),
)


_LAYER_BASES: tuple[type[HoundarrError], ...] = (
    ClientError,
    EngineError,
    ServiceError,
    RouteError,
)


class TestErrorHierarchyShape:
    """Pin the error-class tree so layer moves surface here."""

    @pytest.mark.parametrize("base", _LAYER_BASES)
    def test_layer_base_inherits_from_root(self, base: type[HoundarrError]) -> None:
        """Each of the four layer bases must share the HoundarrError root."""
        assert issubclass(base, HoundarrError)

    @pytest.mark.parametrize(
        ("concrete", "base"),
        _HIERARCHY,
        ids=lambda entry: entry.__name__ if isinstance(entry, type) else str(entry),
    )
    def test_concrete_inherits_from_declared_base(
        self,
        concrete: type[HoundarrError],
        base: type[HoundarrError],
    ) -> None:
        """Each concrete class sits under the correct layer base."""
        assert issubclass(concrete, base)
        assert issubclass(concrete, HoundarrError)

    def test_no_extra_public_exceptions_in_errors_module(self) -> None:
        """Freeze the public error surface.

        A future batch that adds a new HoundarrError subclass must
        update _HIERARCHY here; this test turns that into a forced
        decision instead of a silent drift.
        """
        public_names = {
            name
            for name in dir(errors_module)
            if not name.startswith("_")
            and isinstance(getattr(errors_module, name), type)
            and issubclass(getattr(errors_module, name), HoundarrError)
        }
        expected = {
            "AuthRejectedError",
            "ClientError",
            "ClientHTTPError",
            "ClientRedirectError",
            "ClientTransportError",
            "ClientUnreachableError",
            "ClientValidationError",
            "CooldownStateError",
            "CsrfValidationError",
            "EngineDispatchError",
            "EngineError",
            "EngineOffsetPersistError",
            "EnginePoolFetchError",
            "EngineQueueProbeError",
            "HoundarrError",
            "InstanceValidationError",
            "RouteError",
            "ServiceError",
            "TimeWindowSpecError",
        }
        assert public_names == expected


class TestErrorConstructionAndChaining:
    """Pin that each error is constructible and the cause chain works."""

    @pytest.mark.parametrize(
        "cls",
        [entry.concrete for entry in _HIERARCHY],
    )
    def test_constructs_with_message(self, cls: type[HoundarrError]) -> None:
        """Every concrete class accepts a string message via its Exception base."""
        exc = cls("test message")
        assert str(exc) == "test message"

    @pytest.mark.parametrize(
        "cls",
        [entry.concrete for entry in _HIERARCHY],
    )
    def test_raise_from_preserves_cause(self, cls: type[HoundarrError]) -> None:
        """``raise <typed>(...) from <raw>`` attaches the original on __cause__."""
        original = RuntimeError("root cause")
        try:
            try:
                raise original
            except RuntimeError as exc:
                raise cls("typed wrap") from exc
        except cls as typed_exc:
            assert typed_exc.__cause__ is original


class TestHoundarrErrorIsCatchable:
    """Every Houndarr-defined exception must be catchable via the root."""

    @pytest.mark.parametrize(
        "cls",
        [HoundarrError, *_LAYER_BASES, *[entry.concrete for entry in _HIERARCHY]],
    )
    def test_catches_via_houndarr_error(self, cls: type[HoundarrError]) -> None:
        """Callers that care only about Houndarr-originated errors can catch the root."""
        with pytest.raises(HoundarrError):
            raise cls("x")

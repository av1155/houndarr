"""Tests for the houndarr.errors hierarchy."""

from __future__ import annotations

import pytest

from houndarr.errors import (
    AuthRejectedError,
    ClientError,
    ClientHTTPError,
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


class TestRootInheritance:
    @pytest.mark.parametrize(
        "cls",
        [
            ClientError,
            EngineError,
            ServiceError,
            RouteError,
        ],
    )
    def test_layer_bases_inherit_root(self, cls: type[Exception]) -> None:
        assert issubclass(cls, HoundarrError)
        assert issubclass(cls, Exception)


class TestClientBranch:
    @pytest.mark.parametrize(
        "cls",
        [
            ClientHTTPError,
            ClientTransportError,
            ClientValidationError,
            ClientUnreachableError,
        ],
    )
    def test_concretes_inherit_client_error(self, cls: type[Exception]) -> None:
        assert issubclass(cls, ClientError)
        assert issubclass(cls, HoundarrError)


class TestEngineBranch:
    @pytest.mark.parametrize(
        "cls",
        [
            EngineDispatchError,
            EnginePoolFetchError,
            EngineOffsetPersistError,
            EngineQueueProbeError,
        ],
    )
    def test_concretes_inherit_engine_error(self, cls: type[Exception]) -> None:
        assert issubclass(cls, EngineError)
        assert issubclass(cls, HoundarrError)


class TestServiceBranch:
    @pytest.mark.parametrize(
        "cls",
        [
            InstanceValidationError,
            CooldownStateError,
            TimeWindowSpecError,
        ],
    )
    def test_concretes_inherit_service_error(self, cls: type[Exception]) -> None:
        assert issubclass(cls, ServiceError)
        assert issubclass(cls, HoundarrError)


class TestRouteBranch:
    @pytest.mark.parametrize("cls", [CsrfValidationError, AuthRejectedError])
    def test_concretes_inherit_route_error(self, cls: type[Exception]) -> None:
        assert issubclass(cls, RouteError)
        assert issubclass(cls, HoundarrError)


class TestRaiseAndCatch:
    def test_catch_by_layer_base(self) -> None:
        with pytest.raises(EngineError):
            raise EngineDispatchError("dispatch boom")

    def test_catch_by_root(self) -> None:
        with pytest.raises(HoundarrError):
            raise ClientTransportError("network down")

    def test_does_not_catch_framework_exception(self) -> None:
        """Built-in ``ValueError`` is NOT a HoundarrError."""
        assert not issubclass(ValueError, HoundarrError)

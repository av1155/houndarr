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


class TestInstanceValidationPublicMessage:
    """``InstanceValidationError.public_message`` is the route-safe accessor.

    Routes surface this string in the connection-guard banner instead of
    ``str(exc)`` so chained ``__cause__`` text from a future raise site
    can never leak into the HTTP response.
    """

    def test_returns_first_arg_for_curated_message(self) -> None:
        exc = InstanceValidationError("Invalid instance type.")
        assert exc.public_message == "Invalid instance type."

    def test_empty_when_constructed_with_no_args(self) -> None:
        exc = InstanceValidationError()
        assert exc.public_message == ""

    def test_unchanged_by_from_exc_chaining(self) -> None:
        """``raise X("curated") from cause`` leaves ``args[0]`` curated.

        The original cause is preserved on ``__cause__`` for server-side
        logging while ``public_message`` keeps the user-facing surface
        free of upstream exception text.
        """
        cause = ValueError("internal detail that must not leak")
        try:
            try:
                raise cause
            except ValueError as inner:
                raise InstanceValidationError("Invalid instance type.") from inner
        except InstanceValidationError as exc:
            assert exc.public_message == "Invalid instance type."
            assert exc.__cause__ is cause

    def test_coerces_non_string_first_arg(self) -> None:
        """Belt-and-braces: any future raise site that passes a non-string
        first arg still produces a string for the response body.
        """
        exc = InstanceValidationError(42)
        assert exc.public_message == "42"

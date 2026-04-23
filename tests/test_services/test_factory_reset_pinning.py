"""Pin the typed-error surface on ``factory_reset``.

:func:`~houndarr.services.admin.factory_reset` raises
:class:`~houndarr.errors.ServiceError` for any failure, with the
original exception preserved on ``__cause__``.  The cycle body
lives in :func:`_factory_reset_impl`; the public entrypoint wraps
it in a top-level ``try`` that converts any non-typed ``Exception``
into ``ServiceError`` and passes already-typed ``ServiceError``
through unchanged.

Two wrap paths are pinned here:

* The file-deletion inner catch surfaces ``ServiceError`` with the
  original ``OSError`` (or similar) on ``__cause__``.
* The outer top-level wrap surfaces ``ServiceError`` for any other
  failure shape (e.g. ``RuntimeError`` from a mocked ``init_db``).

The route catch in :mod:`houndarr.routes.admin` covers both so the
hybrid delayed-exit fallback engages on every failure mode the
service layer can raise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from houndarr.errors import ServiceError
from houndarr.services.admin import factory_reset

pytestmark = pytest.mark.pinning


class _FakeAppState:
    """Minimal stand-in for fastapi ``app.state`` that factory_reset mutates."""

    supervisor: Any = None
    master_key: bytes = b"old-key"
    retention_task: Any = None


class _FakeApp:
    """Minimal FastAPI-shaped app; factory_reset only touches ``app.state``."""

    def __init__(self) -> None:
        self.state = _FakeAppState()


@pytest.fixture()
def tmp_data_dir_factory(tmp_path: Path) -> Path:
    """Prepare a data-dir layout that factory_reset's file-delete step can chew on."""
    (tmp_path / "houndarr.db").write_text("sqlite")
    (tmp_path / "houndarr.db-wal").write_text("wal")
    (tmp_path / "houndarr.db-shm").write_text("shm")
    (tmp_path / "houndarr.masterkey").write_bytes(Fernet.generate_key())
    return tmp_path


class TestFactoryResetTypedSurface:
    """Pin the typed wrap on :func:`factory_reset`."""

    @pytest.mark.asyncio()
    async def test_file_deletion_failure_raises_service_error(
        self,
        tmp_data_dir_factory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PermissionError from Path.unlink is wrapped in ServiceError."""
        original = PermissionError("denied")

        def _boom(self: Path) -> None:
            raise original

        monkeypatch.setattr(Path, "unlink", _boom)
        app = _FakeApp()

        with pytest.raises(ServiceError) as exc_info:
            await factory_reset(app=app, data_dir=str(tmp_data_dir_factory))

        assert exc_info.value.__cause__ is original
        assert "file deletion failed" in str(exc_info.value)

    @pytest.mark.asyncio()
    async def test_init_db_failure_raises_service_error(
        self,
        tmp_data_dir_factory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeError from init_db is wrapped by the top-level boundary."""
        original = RuntimeError("init_db crashed")

        async def _boom() -> None:
            raise original

        monkeypatch.setattr("houndarr.services.admin.init_db", _boom)
        app = _FakeApp()

        with pytest.raises(ServiceError) as exc_info:
            await factory_reset(app=app, data_dir=str(tmp_data_dir_factory))

        assert exc_info.value.__cause__ is original
        assert "re-init failed" in str(exc_info.value)

    @pytest.mark.asyncio()
    async def test_ensure_master_key_failure_raises_service_error(
        self,
        tmp_data_dir_factory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError from master-key rotation wraps through the top-level boundary."""
        original = OSError("disk full")

        async def _fake_init_db() -> None:
            return None

        def _boom(_: str) -> bytes:
            raise original

        monkeypatch.setattr("houndarr.services.admin.init_db", _fake_init_db)
        monkeypatch.setattr("houndarr.services.admin.ensure_master_key", _boom)
        app = _FakeApp()

        with pytest.raises(ServiceError) as exc_info:
            await factory_reset(app=app, data_dir=str(tmp_data_dir_factory))

        assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio()
    async def test_service_error_from_impl_passes_through(
        self,
        tmp_data_dir_factory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A ServiceError raised inside _factory_reset_impl is not re-wrapped."""
        inner = ServiceError("already typed")

        async def _boom(*, app: Any, data_dir: str) -> None:
            raise inner

        monkeypatch.setattr("houndarr.services.admin._factory_reset_impl", _boom)
        app = _FakeApp()

        with pytest.raises(ServiceError) as exc_info:
            await factory_reset(app=app, data_dir=str(tmp_data_dir_factory))

        assert exc_info.value is inner

    @pytest.mark.asyncio()
    async def test_impl_returning_normally_does_not_raise(
        self,
        tmp_data_dir_factory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path: if _factory_reset_impl returns, the wrapper does too.

        Stubs the inner impl so this test exercises the wrapper contract
        alone; the end-to-end happy path is covered by
        ``tests/test_services/test_admin.py``.
        """

        async def _noop(*, app: Any, data_dir: str) -> None:
            return None

        monkeypatch.setattr("houndarr.services.admin._factory_reset_impl", _noop)
        app = _FakeApp()

        result = await factory_reset(app=app, data_dir=str(tmp_data_dir_factory))

        assert result is None

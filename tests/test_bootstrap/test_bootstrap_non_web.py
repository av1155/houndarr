"""Pinning tests for ``houndarr.bootstrap.bootstrap_non_web``.

Locks the four-step composition extracted from the three pre-existing
call sites (the CLI pre-uvicorn phase, ``seed_demo_data.py``, and
``serve_demo.py``). Every branch and boundary is covered so later
callers can depend on: tuple shape, singleton pinning rules, idempotent
init_db, and the Fernet master-key persistence contract.
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from pathlib import Path

import pytest

from houndarr import config as _cfg
from houndarr.bootstrap import bootstrap_non_web
from houndarr.config import AppSettings, bootstrap_settings

pytestmark = pytest.mark.pinning


@pytest.fixture(autouse=True)
def _reset_config_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Clear the pinned singleton + HOUNDARR_DATA_DIR before and after each test.

    The config module caches resolved settings in ``_runtime_settings``;
    without this reset, test ordering would determine which path inside
    :func:`bootstrap_non_web` runs. ``test_update_check_repo_override``
    in particular leaves the pin set to a non-default ``update_check_repo``
    that would otherwise leak into downstream test files (the
    ``test_services/test_update_check.py`` mocks would miss the leaked
    repo URL and respx would raise ``AllMockedAssertionError``). The
    teardown ``bootstrap_settings()`` mirrors the ``_isolate_pin``
    pattern in ``tests/test_config.py``: clear before to defend against
    upstream pollution, clear after to defend downstream tests against
    ours. The env delete provides the same isolation for the
    env-fallback branch.
    """
    monkeypatch.delenv("HOUNDARR_DATA_DIR", raising=False)
    # bootstrap_settings() with no overrides clears the pin (see config.py).
    bootstrap_settings()
    yield
    bootstrap_settings()


class TestReturnShape:
    """Pin the three-tuple shape the plan fixes as the public contract."""

    def test_returns_three_tuple(self, tmp_path: Path) -> None:
        result = bootstrap_non_web(data_dir=str(tmp_path))
        assert len(result) == 3

    def test_first_is_app_settings(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert isinstance(settings, AppSettings)

    def test_second_is_path(self, tmp_path: Path) -> None:
        _settings, db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert isinstance(db_path, Path)

    def test_third_is_bytes(self, tmp_path: Path) -> None:
        _settings, _db_path, key = bootstrap_non_web(data_dir=str(tmp_path))
        assert isinstance(key, bytes)


class TestSettingsContents:
    """The returned :class:`AppSettings` reflects the requested data_dir."""

    def test_data_dir_matches_argument(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert settings.data_dir == str(tmp_path)

    def test_db_path_derived_from_data_dir(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert settings.db_path == tmp_path / "houndarr.db"

    def test_master_key_path_derived_from_data_dir(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert settings.master_key_path == tmp_path / "houndarr.masterkey"

    def test_returned_db_path_matches_settings(self, tmp_path: Path) -> None:
        settings, db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert db_path == settings.db_path


class TestDbInit:
    """Pin the idempotent init_db contract and on-disk side effects."""

    def test_db_file_exists_after_init(self, tmp_path: Path) -> None:
        _settings, db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert db_path.exists()

    def test_idempotent_second_call_succeeds(self, tmp_path: Path) -> None:
        bootstrap_non_web(data_dir=str(tmp_path))
        # A second call against the same data dir must not raise: init_db
        # is idempotent and ensure_master_key returns the existing key.
        settings, db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert settings.data_dir == str(tmp_path)
        assert db_path.exists()

    def test_schema_version_is_pinned(self, tmp_path: Path) -> None:
        import asyncio

        from houndarr.database import get_db

        bootstrap_non_web(data_dir=str(tmp_path))

        async def _read_version() -> str | None:
            async with get_db() as db:
                async with db.execute(
                    "SELECT value FROM settings WHERE key = 'schema_version'"
                ) as cur:
                    row = await cur.fetchone()
            return None if row is None else str(row["value"])

        version = asyncio.run(_read_version())
        assert version is not None
        assert int(version) >= 13


class TestMasterKey:
    """Pin the Fernet master-key invariants."""

    def test_master_key_file_created(self, tmp_path: Path) -> None:
        _settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert (tmp_path / "houndarr.masterkey").exists()

    def test_master_key_is_fernet_shaped(self, tmp_path: Path) -> None:
        _settings, _db_path, key = bootstrap_non_web(data_dir=str(tmp_path))
        # Fernet keys decode from URL-safe base64 to exactly 32 bytes.
        assert len(base64.urlsafe_b64decode(key)) == 32

    def test_master_key_persists_across_calls(self, tmp_path: Path) -> None:
        _s1, _d1, key1 = bootstrap_non_web(data_dir=str(tmp_path))
        _s2, _d2, key2 = bootstrap_non_web(data_dir=str(tmp_path))
        assert key1 == key2


class TestDataDirCreation:
    """Nested data dirs must be created eagerly before crypto + DB run."""

    def test_nested_data_dir_is_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "deeply" / "nested" / "dir"
        assert not nested.exists()
        bootstrap_non_web(data_dir=str(nested))
        assert nested.is_dir()
        assert (nested / "houndarr.db").exists()
        assert (nested / "houndarr.masterkey").exists()

    def test_pre_existing_data_dir_is_fine(self, tmp_path: Path) -> None:
        # mkdir(exist_ok=True) must not raise when the dir already exists.
        existing = tmp_path / "already"
        existing.mkdir()
        settings, db_path, _key = bootstrap_non_web(data_dir=str(existing))
        assert settings.data_dir == str(existing)
        assert db_path.exists()


class TestEnvPropagation:
    """HOUNDARR_DATA_DIR must land in the process env for reload children."""

    def test_env_is_set_to_data_dir(self, tmp_path: Path) -> None:
        import os

        bootstrap_non_web(data_dir=str(tmp_path))
        assert os.environ["HOUNDARR_DATA_DIR"] == str(tmp_path)

    def test_env_is_overwritten_on_subsequent_call(self, tmp_path: Path) -> None:
        import os

        first = tmp_path / "first"
        second = tmp_path / "second"
        bootstrap_non_web(data_dir=str(first))
        assert os.environ["HOUNDARR_DATA_DIR"] == str(first)
        bootstrap_non_web(data_dir=str(second))
        assert os.environ["HOUNDARR_DATA_DIR"] == str(second)


class TestRuntimeSettingsPinning:
    """The ``_runtime_settings`` singleton is only pinned when overrides win."""

    def test_no_overrides_leaves_singleton_unpinned(self, tmp_path: Path) -> None:
        # With no overrides, callers fall back to env-var resolution via
        # get_settings() on every call. Pinning would trap the derived
        # object and break later env changes, so we explicitly do not.
        bootstrap_non_web(data_dir=str(tmp_path))
        assert _cfg._runtime_settings is None  # noqa: SLF001

    def test_overrides_pin_the_singleton(self, tmp_path: Path) -> None:
        bootstrap_non_web(data_dir=str(tmp_path), port=9999)
        assert _cfg._runtime_settings is not None  # noqa: SLF001
        assert _cfg._runtime_settings.port == 9999  # noqa: SLF001

    def test_prior_pin_is_cleared_before_resolving(self, tmp_path: Path) -> None:
        # A stale pin from a previous run must not leak into the new one.
        bootstrap_settings(data_dir="/stale", port=1234)
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path))
        assert settings.data_dir == str(tmp_path)
        assert settings.port != 1234


class TestOverrides:
    """Every documented override key flows through to AppSettings."""

    def test_host_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path), host="127.0.0.1")
        assert settings.host == "127.0.0.1"

    def test_port_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path), port=9000)
        assert settings.port == 9000

    def test_dev_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path), dev=True)
        assert settings.dev is True

    def test_log_level_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path), log_level="debug")
        assert settings.log_level == "debug"

    def test_secure_cookies_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(data_dir=str(tmp_path), secure_cookies=True)
        assert settings.secure_cookies is True

    def test_cookie_samesite_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(
            data_dir=str(tmp_path), cookie_samesite="strict"
        )
        assert settings.cookie_samesite == "strict"

    def test_trusted_proxies_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(
            data_dir=str(tmp_path), trusted_proxies="10.0.0.1,172.18.0.0/16"
        )
        assert settings.trusted_proxies == "10.0.0.1,172.18.0.0/16"

    def test_auth_mode_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(
            data_dir=str(tmp_path),
            auth_mode="proxy",
            auth_proxy_header="Remote-User",
            trusted_proxies="10.0.0.1",
        )
        assert settings.auth_mode == "proxy"
        assert settings.auth_proxy_header == "Remote-User"

    def test_update_check_repo_override(self, tmp_path: Path) -> None:
        settings, _db_path, _key = bootstrap_non_web(
            data_dir=str(tmp_path), update_check_repo="forker/houndarr-fork"
        )
        assert settings.update_check_repo == "forker/houndarr-fork"

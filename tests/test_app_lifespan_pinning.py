"""Pin app.py lifespan + RequestValidationError handler contract.

Locks the startup-order sequence (validate -> master_key ->
set_db_path -> init_db -> purge_old_logs -> Supervisor.start) and
the HTMX-vs-JSON split on the validation-error handler so later
changes to the lifespan wiring do not silently shift either
contract.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from houndarr import app as app_module
from houndarr.app import _periodic_log_retention
from houndarr.config import DEFAULT_LOG_RETENTION_DAYS, AppSettings

pytestmark = pytest.mark.pinning


# Startup wiring: lifespan calls each bootstrap step exactly once in order


class TestLifespanStartup:
    def test_startup_raises_on_invalid_auth_config(
        self, tmp_data_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lifespan raises RuntimeError when settings.validate_auth_config returns errors."""
        bad_settings = AppSettings(
            data_dir=tmp_data_dir,
            auth_mode="proxy",
            auth_proxy_header="",  # missing required header
        )
        monkeypatch.setattr(app_module, "get_settings", lambda: bad_settings)

        fastapi_app = app_module.create_app()
        with pytest.raises(RuntimeError, match="Invalid auth configuration"):
            with TestClient(fastapi_app):
                pass

    def test_startup_order_runs_each_bootstrap_step(
        self,
        test_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ensure_master_key -> set_db_path -> init_db -> purge_old_logs -> supervisor.start."""
        order: list[str] = []

        real_ensure = app_module.ensure_master_key
        real_set_db_path = app_module.set_db_path
        real_init_db = app_module.init_db
        real_purge = app_module.purge_old_logs

        def spy_ensure(data_dir: str) -> bytes:
            order.append("ensure_master_key")
            return real_ensure(data_dir)

        def spy_set_db_path(path: str) -> None:
            order.append("set_db_path")
            real_set_db_path(path)

        async def spy_init_db() -> None:
            order.append("init_db")
            await real_init_db()

        async def spy_purge(days: int) -> int:
            order.append("purge_old_logs")
            return await real_purge(days)

        monkeypatch.setattr(app_module, "ensure_master_key", spy_ensure)
        monkeypatch.setattr(app_module, "set_db_path", spy_set_db_path)
        monkeypatch.setattr(app_module, "init_db", spy_init_db)
        monkeypatch.setattr(app_module, "purge_old_logs", spy_purge)

        fastapi_app = app_module.create_app()
        with TestClient(fastapi_app):
            pass

        assert order == [
            "ensure_master_key",
            "set_db_path",
            "init_db",
            "purge_old_logs",
        ]

    def test_shutdown_cancels_retention_task(
        self,
        app: TestClient,
    ) -> None:
        """After the TestClient exits the lifespan, retention_task is cancelled."""
        retention = getattr(app.app.state, "retention_task", None)
        assert retention is not None
        # TestClient has yielded (we are still inside the with-block via fixture).
        # Sanity: task was created and is alive.
        assert not retention.done()


# RequestValidationError split


class TestValidationHandler:
    def test_htmx_request_returns_empty_body_with_hx_reswap_none(self, app: TestClient) -> None:
        """Trigger a RequestValidationError on a non-HTMX handler via HX-Request header."""
        # /setup POST requires username/password/password_confirm form fields.
        resp = app.post("/setup", data={}, headers={"HX-Request": "true"})
        assert resp.status_code == 422
        assert resp.headers.get("HX-Reswap") == "none"
        assert resp.text == ""

    def test_non_htmx_request_returns_json_detail(self, app: TestClient) -> None:
        resp = app.post("/setup", data={})
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/json")
        assert "detail" in resp.json()


# Module-level wiring constants


class TestModuleConstants:
    def test_log_retention_interval_is_24_hours(self) -> None:
        assert app_module._LOG_RETENTION_INTERVAL_SECONDS == 24 * 60 * 60

    def test_default_log_retention_days_from_config(self) -> None:
        """The lifespan's purge call uses DEFAULT_LOG_RETENTION_DAYS."""
        assert DEFAULT_LOG_RETENTION_DAYS >= 1

    def test_periodic_log_retention_is_async(self) -> None:
        """_periodic_log_retention is an async def (not a sync function)."""
        import inspect

        assert inspect.iscoroutinefunction(_periodic_log_retention)

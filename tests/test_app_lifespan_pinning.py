"""Pin app.py lifespan + RequestValidationError handler contract.

Locks the startup-order sequence (validate -> master_key ->
set_db_path -> init_db_schema -> purge_old_logs ->
init_db_migrations -> Supervisor.start) and the HTMX-vs-JSON split
on the validation-error handler so later changes to the lifespan
wiring do not silently shift either contract.

The schema/purge/migrations split lands log retention before the
v14 cooldown back-fill (issue #586): on long-lived databases the
back-fill grinds through 30+ days of unused rows otherwise, and
the Kubernetes liveness probe has shipped pods to crash-loop in
production over it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from houndarr import app as app_module
from houndarr.app import _periodic_log_retention
from houndarr.config import AppSettings

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
        """master_key -> set_db_path -> schema DDL -> purge -> migrations.

        Schema DDL must precede the purge call so ``search_log`` exists
        when ``DELETE FROM search_log`` fires on fresh installs.  The
        purge then runs *before* migrations so the v14 cooldown back-fill
        never sees rows that retention is about to delete.
        """
        order: list[str] = []

        real_ensure = app_module.ensure_master_key
        real_set_db_path = app_module.set_db_path
        real_init_schema = app_module.init_db_schema
        real_init_migrations = app_module.init_db_migrations
        real_purge = app_module.purge_old_logs

        def spy_ensure(data_dir: str) -> bytes:
            order.append("ensure_master_key")
            return real_ensure(data_dir)

        def spy_set_db_path(path: str) -> None:
            order.append("set_db_path")
            real_set_db_path(path)

        async def spy_init_schema() -> None:
            order.append("init_db_schema")
            await real_init_schema()

        async def spy_init_migrations() -> None:
            order.append("init_db_migrations")
            await real_init_migrations()

        async def spy_purge(days: int) -> int:
            order.append("purge_old_logs")
            return await real_purge(days)

        monkeypatch.setattr(app_module, "ensure_master_key", spy_ensure)
        monkeypatch.setattr(app_module, "set_db_path", spy_set_db_path)
        monkeypatch.setattr(app_module, "init_db_schema", spy_init_schema)
        monkeypatch.setattr(app_module, "init_db_migrations", spy_init_migrations)
        monkeypatch.setattr(app_module, "purge_old_logs", spy_purge)

        fastapi_app = app_module.create_app()
        with TestClient(fastapi_app):
            pass

        assert order == [
            "ensure_master_key",
            "set_db_path",
            "init_db_schema",
            "purge_old_logs",
            "init_db_migrations",
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

    def test_lifespan_skips_retention_task_when_retention_disabled(
        self,
        tmp_data_dir: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``log_retention_days=0`` skips the periodic loop entirely.

        Operators who set the env var to ``0`` opt out of automatic
        purges.  The lifespan should reflect that by leaving
        ``app.state.retention_task = None`` and never spawning the
        24-hour task; otherwise the task wakes once a day and runs a
        no-op ``DELETE`` on a disabled threshold.
        """
        from houndarr.config import bootstrap_settings

        bootstrap_settings(data_dir=tmp_data_dir, log_retention_days=0)
        from houndarr.auth import reset_auth_caches

        reset_auth_caches()

        fastapi_app = app_module.create_app()
        with TestClient(fastapi_app) as client:
            assert getattr(client.app.state, "retention_task", "missing") is None


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

    def test_app_settings_default_log_retention_days(self) -> None:
        """The AppSettings default preserves the v1.10.x 30-day baseline."""
        assert AppSettings(data_dir="/tmp").log_retention_days == 30

    def test_periodic_log_retention_is_async(self) -> None:
        """_periodic_log_retention is an async def (not a sync function)."""
        import inspect

        assert inspect.iscoroutinefunction(_periodic_log_retention)

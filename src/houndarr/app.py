"""FastAPI application factory with lifespan, middleware, and route registration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from houndarr import __version__
from houndarr.auth import AuthMiddleware
from houndarr.config import DEFAULT_LOG_RETENTION_DAYS, get_settings
from houndarr.crypto import ensure_master_key
from houndarr.database import init_db, set_db_path
from houndarr.engine.supervisor import Supervisor
from houndarr.repositories.search_log import purge_old_logs
from houndarr.routes._htmx import is_hx_request
from houndarr.services.instances import list_instances

logger = logging.getLogger(__name__)

_LOG_RETENTION_INTERVAL_SECONDS = 24 * 60 * 60


async def _periodic_log_retention() -> None:
    """Periodically purge old search_log rows during app uptime."""
    while True:
        await asyncio.sleep(_LOG_RETENTION_INTERVAL_SECONDS)
        try:
            purged = await purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
            if purged > 0:
                logger.info(
                    "Periodic retention purged %d search_log rows older than %d days",
                    purged,
                    DEFAULT_LOG_RETENTION_DAYS,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Periodic log retention task failed")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialize DB on startup, clean up on shutdown."""
    settings = get_settings()

    # Defense-in-depth: validate auth config even if __main__ already did.
    # Covers cases where create_app() is called directly (tests, ASGI server).
    auth_errors = settings.validate_auth_config()
    if auth_errors:
        for err in auth_errors:
            logger.critical("Configuration error: %s", err)
        msg = "Invalid auth configuration; see log for details"
        raise RuntimeError(msg)

    # Ensure data directory exists
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)

    # Load (or generate) the master encryption key and store on app state
    app.state.master_key = ensure_master_key(settings.data_dir)
    logger.info("Master key loaded from %s", settings.master_key_path)

    # Configure and initialize the database
    set_db_path(str(settings.db_path))
    await init_db()
    logger.info("Database ready at %s", settings.db_path)

    # Purge old search log rows to prevent unbounded growth
    purged = await purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
    if purged > 0:
        logger.info(
            "Purged %d search_log rows older than %d days", purged, DEFAULT_LOG_RETENTION_DAYS
        )

    # Warn if no instances are configured yet
    instances = await list_instances(master_key=app.state.master_key)
    if not instances:
        logger.warning("No instances configured. Visit the Settings page to add an instance.")

    # Start the background search supervisor
    supervisor = Supervisor(master_key=app.state.master_key)
    await supervisor.start()
    app.state.supervisor = supervisor

    retention_task = asyncio.create_task(_periodic_log_retention(), name="log-retention-loop")
    app.state.retention_task = retention_task

    yield  # Application runs here

    logger.info("Houndarr shutting down")
    retention_task.cancel()
    with suppress(asyncio.CancelledError):
        await retention_task
    await supervisor.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Houndarr",
        description=(
            "A focused, self-hosted companion for Radarr, Sonarr, Lidarr, Readarr, and Whisparr."
        ),
        version=__version__,
        docs_url="/api/docs" if settings.dev else None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    # -----------------------------------------------------------------------
    # Static files
    # -----------------------------------------------------------------------
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # -----------------------------------------------------------------------
    # Exception handlers
    # -----------------------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> HTMLResponse | JSONResponse:
        """Return a harmless HTML body (never JSON) for HTMX-initiated
        validation errors.

        Base.html opts 422 into the HTMX swap so server-emitted error
        snippets actually render.  FastAPI's default 422 body is JSON
        (``{"detail": [...]}``), which would render as literal text
        inside whatever slot the form targets.  For HTMX requests we
        ship an empty body with ``HX-Reswap: none`` so the swap is
        suppressed (same visible behaviour as before the config flip)
        and log the validation detail server-side for the operator.

        Non-HTMX clients keep FastAPI's default JSON response so API
        consumers and tests are unaffected.
        """
        if is_hx_request(request):
            logger.warning(
                "HTMX validation error on %s %s: %s",
                request.method,
                request.url.path,
                exc.errors(),
            )
            return HTMLResponse(
                content="",
                status_code=422,
                headers={"HX-Reswap": "none"},
            )
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    # -----------------------------------------------------------------------
    # Middleware (order matters: outermost = first to receive request)
    # -----------------------------------------------------------------------
    app.add_middleware(AuthMiddleware)

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------
    from houndarr.routes.admin import router as admin_router
    from houndarr.routes.api.logs import router as logs_router
    from houndarr.routes.api.status import router as status_router
    from houndarr.routes.changelog import router as changelog_router
    from houndarr.routes.health import router as health_router
    from houndarr.routes.pages import router as pages_router
    from houndarr.routes.settings import router as settings_router

    app.include_router(health_router)
    app.include_router(status_router)
    app.include_router(logs_router)
    app.include_router(pages_router)
    app.include_router(settings_router)
    app.include_router(changelog_router)
    app.include_router(admin_router)

    return app

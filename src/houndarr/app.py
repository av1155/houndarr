"""FastAPI application factory with lifespan, middleware, and route registration."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from houndarr import __version__
from houndarr.auth import AuthMiddleware
from houndarr.config import get_settings
from houndarr.database import init_db, set_db_path

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialize DB on startup, clean up on shutdown."""
    settings = get_settings()

    # Ensure data directory exists
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)

    # Configure and initialize the database
    set_db_path(str(settings.db_path))
    await init_db()
    logger.info("Database ready at %s", settings.db_path)

    yield  # Application runs here

    logger.info("Houndarr shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Houndarr",
        description="A focused, self-hosted companion for Sonarr and Radarr.",
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
    # Middleware (order matters: outermost = first to receive request)
    # -----------------------------------------------------------------------
    app.add_middleware(AuthMiddleware)

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------
    from houndarr.routes.health import router as health_router
    from houndarr.routes.pages import router as pages_router

    app.include_router(health_router)
    app.include_router(pages_router)

    return app

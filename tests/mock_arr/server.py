"""Entry point for the mock *arr server.

Run with ``python -m tests.mock_arr.server --port 9100 --items 500``. The
server mounts six routers under distinct path prefixes so a single uvicorn
process can pretend to be every supported *arr type. Houndarr instances
are configured with URLs of the form ``http://localhost:9100/<app>``.

The mock accepts any (or no) API key. Authentication is intentionally
out of scope: the goal is to exercise Houndarr's pagination and search
dispatch logic, not to model *arr auth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import click
import uvicorn
from fastapi import FastAPI

from tests.mock_arr.routers.lidarr import make_lidarr_data, make_lidarr_router
from tests.mock_arr.routers.radarr import make_radarr_data, make_radarr_router
from tests.mock_arr.routers.readarr import make_readarr_data, make_readarr_router
from tests.mock_arr.routers.sonarr import make_sonarr_data, make_sonarr_router
from tests.mock_arr.routers.whisparr_v2 import (
    make_whisparr_v2_data,
    make_whisparr_v2_router,
)
from tests.mock_arr.routers.whisparr_v3 import (
    make_whisparr_v3_data,
    make_whisparr_v3_router,
)
from tests.mock_arr.store import MockState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SeedConfig:
    """Per-app record counts for ``create_app``.

    Defaults give every app a comfortably large library so the random
    search algorithm runs in the ``N >= K`` regime where it is uniform.
    Lower the counts to reproduce the small-library bias.
    """

    seed: int = 42
    sonarr_series: int = 50
    sonarr_episodes_per_series: int = 10
    radarr_movies: int = 500
    lidarr_artists: int = 50
    lidarr_albums_per_artist: int = 10
    readarr_authors: int = 50
    readarr_books_per_author: int = 10
    whisparr_v2_series: int = 30
    whisparr_v2_episodes_per_series: int = 10
    whisparr_v3_movies: int = 500
    missing_ratio: float = 0.5
    cutoff_ratio: float = 0.2


def build_state(config: SeedConfig) -> MockState:
    """Seed every app's data using the same master seed."""
    return MockState(
        sonarr=make_sonarr_data(
            seed=config.seed,
            series_count=config.sonarr_series,
            episodes_per_series=config.sonarr_episodes_per_series,
            missing_ratio=config.missing_ratio,
            cutoff_ratio=config.cutoff_ratio,
        ),
        radarr=make_radarr_data(
            seed=config.seed,
            movie_count=config.radarr_movies,
            missing_ratio=config.missing_ratio,
            cutoff_ratio=config.cutoff_ratio,
        ),
        lidarr=make_lidarr_data(
            seed=config.seed,
            artist_count=config.lidarr_artists,
            albums_per_artist=config.lidarr_albums_per_artist,
            missing_ratio=config.missing_ratio,
            cutoff_ratio=config.cutoff_ratio,
        ),
        readarr=make_readarr_data(
            seed=config.seed,
            author_count=config.readarr_authors,
            books_per_author=config.readarr_books_per_author,
            missing_ratio=config.missing_ratio,
            cutoff_ratio=config.cutoff_ratio,
        ),
        whisparr_v2=make_whisparr_v2_data(
            seed=config.seed,
            series_count=config.whisparr_v2_series,
            episodes_per_series=config.whisparr_v2_episodes_per_series,
            missing_ratio=config.missing_ratio,
            cutoff_ratio=config.cutoff_ratio,
        ),
        whisparr_v3=make_whisparr_v3_data(
            seed=config.seed,
            movie_count=config.whisparr_v3_movies,
            missing_ratio=config.missing_ratio,
            cutoff_ratio=config.cutoff_ratio,
        ),
    )


def create_app(config: SeedConfig | None = None) -> FastAPI:
    """Build the FastAPI app with all six routers mounted."""
    cfg = config or SeedConfig()
    state = build_state(cfg)
    app = FastAPI(
        title="Houndarr Mock *arr Server",
        description="Seeded multi-app mock for testing Houndarr's search engine.",
    )

    app.include_router(make_sonarr_router(state.sonarr))
    app.include_router(make_radarr_router(state.radarr))
    app.include_router(make_lidarr_router(state.lidarr))
    app.include_router(make_readarr_router(state.readarr))
    app.include_router(make_whisparr_v2_router(state.whisparr_v2))
    app.include_router(make_whisparr_v3_router(state.whisparr_v3))

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "name": "Houndarr Mock *arr Server",
            "endpoints": {
                "sonarr": "http://HOST:PORT/sonarr",
                "radarr": "http://HOST:PORT/radarr",
                "lidarr": "http://HOST:PORT/lidarr",
                "readarr": "http://HOST:PORT/readarr",
                "whisparr_v2": "http://HOST:PORT/whisparr_v2",
                "whisparr_v3": "http://HOST:PORT/whisparr_v3",
            },
            "totals": {
                "sonarr": _summary(state.sonarr),
                "radarr": _summary(state.radarr),
                "lidarr": _summary(state.lidarr),
                "readarr": _summary(state.readarr),
                "whisparr_v2": _summary(state.whisparr_v2),
                "whisparr_v3": _summary(state.whisparr_v3),
            },
        }

    @app.get("/__commands__/{app_name}")
    async def get_commands(app_name: str) -> dict[str, Any]:
        """Return every POSTed command captured for one app.

        Useful for tests that want to assert which leaves the engine
        actually dispatched a search for during a cycle.
        """
        store = getattr(state, app_name, None)
        if store is None:
            return {"error": f"unknown app: {app_name}"}
        return {"commands": store.command_log.entries}

    @app.get("/__page_log__/{app_name}")
    async def get_page_log(app_name: str) -> dict[str, Any]:
        """Return every paginated wanted request captured for one app.

        Each entry is ``[kind, page, page_size]`` where kind is one of
        ``"missing"`` or ``"cutoff"``. This is the ground-truth log for
        verifying the random search algorithm's page-distribution
        fairness.
        """
        store = getattr(state, app_name, None)
        if store is None:
            return {"error": f"unknown app: {app_name}"}
        return {"entries": list(store.page_log.entries)}

    @app.post("/__reset__/{app_name}")
    async def reset_app(app_name: str) -> dict[str, Any]:
        """Wipe command and page logs for one app, leaving seed data intact."""
        store = getattr(state, app_name, None)
        if store is None:
            return {"error": f"unknown app: {app_name}"}
        store.command_log.entries.clear()
        store.page_log.entries.clear()
        return {"reset": app_name}

    app.state.mock_state = state
    return app


def _summary(data: object) -> dict[str, int]:
    leaves: list[Any] = getattr(data, "leaves", [])
    missing: set[int] = getattr(data, "missing_ids", set())
    cutoff: set[int] = getattr(data, "cutoff_ids", set())
    upgrade: set[int] = getattr(data, "upgrade_ids", set())
    return {
        "total": len(leaves),
        "missing": len(missing),
        "cutoff": len(cutoff),
        "upgrade": len(upgrade),
    }


@click.command()
@click.option("--port", default=9100, show_default=True, help="Port to bind.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--seed", default=42, show_default=True, help="Master RNG seed.")
@click.option(
    "--items",
    default=500,
    show_default=True,
    help="Approximate leaf count per *arr app (overrides per-app counts).",
)
@click.option("--missing-ratio", default=0.5, show_default=True)
@click.option("--cutoff-ratio", default=0.2, show_default=True)
@click.option("--log-level", default="info", show_default=True)
def main(
    port: int,
    host: str,
    seed: int,
    items: int,
    missing_ratio: float,
    cutoff_ratio: float,
    log_level: str,
) -> None:
    """Launch the mock server."""
    parents = max(10, items // 10)
    leaves_per_parent = max(1, items // parents)
    config = SeedConfig(
        seed=seed,
        sonarr_series=parents,
        sonarr_episodes_per_series=leaves_per_parent,
        radarr_movies=items,
        lidarr_artists=parents,
        lidarr_albums_per_artist=leaves_per_parent,
        readarr_authors=parents,
        readarr_books_per_author=leaves_per_parent,
        whisparr_v2_series=max(10, parents // 2),
        whisparr_v2_episodes_per_series=leaves_per_parent,
        whisparr_v3_movies=items,
        missing_ratio=missing_ratio,
        cutoff_ratio=cutoff_ratio,
    )
    app = create_app(config)
    uvicorn.run(app, host=host, port=port, log_level=log_level)


app = create_app()


if __name__ == "__main__":
    main()

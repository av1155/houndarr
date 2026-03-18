"""Entry point: python -m houndarr."""

from __future__ import annotations

import click

from houndarr import __version__


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="houndarr")
@click.option(
    "--data-dir",
    default="/data",
    show_default=True,
    envvar="HOUNDARR_DATA_DIR",
    help="Directory for persistent data (SQLite DB and master key).",
)
@click.option(
    "--host",
    default="0.0.0.0",
    show_default=True,
    envvar="HOUNDARR_HOST",
    help="Host address to bind the web server to.",
)
@click.option(
    "--port",
    default=8877,
    show_default=True,
    envvar="HOUNDARR_PORT",
    type=int,
    help="Port to bind the web server to.",
)
@click.option(
    "--dev",
    is_flag=True,
    default=False,
    help="Run in development mode with auto-reload.",
)
@click.option(
    "--log-level",
    default="info",
    show_default=True,
    type=click.Choice(["debug", "info", "warning", "error"], case_sensitive=False),
    envvar="HOUNDARR_LOG_LEVEL",
    help="Log level for the web server.",
)
@click.option(
    "--secure-cookies",
    is_flag=True,
    default=False,
    envvar="HOUNDARR_SECURE_COOKIES",
    help=(
        "Set the Secure flag on session cookies. "
        "Enable when serving Houndarr over HTTPS via a reverse proxy."
    ),
)
@click.option(
    "--trusted-proxies",
    default="",
    show_default=False,
    envvar="HOUNDARR_TRUSTED_PROXIES",
    help=(
        "Comma-separated list of trusted reverse-proxy IP addresses. "
        "When set, X-Forwarded-For is honoured for client-IP detection "
        "(used for login rate limiting). "
        "Example: '10.0.0.1,172.16.0.1'."
    ),
)
def cli(
    data_dir: str,
    host: str,
    port: int,
    dev: bool,
    log_level: str,
    secure_cookies: bool,
    trusted_proxies: str,
) -> None:
    """Houndarr — search for missing media in your *arr stack, politely.

    A focused self-hosted companion that automatically triggers searches for
    missing and cutoff-unmet media in controlled batches, keeping your
    indexers happy.
    """
    import logging

    import uvicorn

    from houndarr.config import AppSettings

    # Configure the root logger so that application loggers (houndarr.*)
    # respect --log-level.  Without this, only uvicorn's own loggers are
    # configured and all houndarr.* INFO messages are silently dropped
    # (Python's root logger defaults to WARNING).
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(levelname)s:     %(message)s",
    )

    settings = AppSettings(
        data_dir=data_dir,
        host=host,
        port=port,
        dev=dev,
        log_level=log_level.lower(),
        secure_cookies=secure_cookies,
        trusted_proxies=trusted_proxies,
    )

    # Store settings so the app factory can pick them up
    import houndarr.config as _cfg

    _cfg._runtime_settings = settings  # noqa: SLF001

    uvicorn.run(
        "houndarr.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=dev,
        log_level=log_level.lower(),
        access_log=dev,
    )


if __name__ == "__main__":
    cli()

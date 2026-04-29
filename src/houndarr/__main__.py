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
    "--cookie-samesite",
    default="lax",
    show_default=True,
    type=click.Choice(["lax", "strict"], case_sensitive=False),
    envvar="HOUNDARR_COOKIE_SAMESITE",
    help=(
        "SameSite attribute for session and CSRF cookies. "
        "'lax' (default) allows cookies on top-level navigations from "
        "external links (e.g. dashboard apps). 'strict' withholds cookies "
        "on all cross-site requests."
    ),
)
@click.option(
    "--trusted-proxies",
    default="",
    show_default=False,
    envvar="HOUNDARR_TRUSTED_PROXIES",
    help=(
        "Comma-separated list of trusted reverse-proxy IP addresses or "
        "CIDR subnets.  When set, X-Forwarded-For is honoured for "
        "client-IP detection (used for login rate limiting).  "
        "Example: '10.0.0.1,172.18.0.0/16'."
    ),
)
@click.option(
    "--auth-mode",
    default="builtin",
    show_default=True,
    type=click.Choice(["builtin", "proxy"], case_sensitive=False),
    envvar="HOUNDARR_AUTH_MODE",
    help=(
        "Authentication mode.  'builtin' uses local session-based auth.  "
        "'proxy' delegates authentication to a reverse proxy via a trusted "
        "header (requires --auth-proxy-header and --trusted-proxies)."
    ),
)
@click.option(
    "--auth-proxy-header",
    default="",
    show_default=False,
    envvar="HOUNDARR_AUTH_PROXY_HEADER",
    help=(
        "HTTP header carrying the authenticated username from the reverse "
        "proxy.  Required when --auth-mode=proxy.  "
        "Common values: 'Remote-User' (Authelia), 'X-authentik-username' "
        "(Authentik), 'X-Auth-Request-User' (oauth2-proxy)."
    ),
)
@click.option(
    "--log-retention-days",
    default="",
    show_default=False,
    envvar="HOUNDARR_LOG_RETENTION_DAYS",
    help=(
        "Number of days of search log rows to keep during the daily "
        "retention sweep.  '0' disables automatic purges; '7' to '365' "
        "overrides the default of 30 days.  Operators on small storage "
        "lower this to keep the dashboard responsive on long-lived "
        "instances; large libraries can extend it.  See issue #586."
    ),
)
def cli(
    data_dir: str,
    host: str,
    port: int,
    dev: bool,
    log_level: str,
    secure_cookies: bool,
    cookie_samesite: str,
    trusted_proxies: str,
    auth_mode: str,
    auth_proxy_header: str,
    log_retention_days: str,
) -> None:
    """Houndarr: search for missing media in your *arr stack, politely.

    A focused self-hosted companion that automatically triggers searches for
    missing and cutoff-unmet media in controlled batches, keeping your
    indexers happy.
    """
    import logging
    import os

    import uvicorn

    from houndarr.bootstrap import bootstrap_non_web
    from houndarr.config import _parse_log_retention_days, _parse_samesite

    # Configure the root logger so that application loggers (houndarr.*)
    # respect --log-level.  Without this, only uvicorn's own loggers are
    # configured and all houndarr.* INFO messages are silently dropped
    # (Python's root logger defaults to WARNING).
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(levelname)s:     %(message)s",
    )

    # Shared non-web bootstrap: pin AppSettings with CLI overrides, ensure
    # the data dir, load the Fernet master key, and run init_db. The
    # FastAPI lifespan re-runs the same idempotent primitives once uvicorn
    # spawns (or reloads) the ASGI child, so running them here is safe.
    settings, _db_path, _master_key = bootstrap_non_web(
        data_dir=data_dir,
        host=host,
        port=port,
        dev=dev,
        log_level=log_level.lower(),
        secure_cookies=secure_cookies,
        cookie_samesite=_parse_samesite(cookie_samesite),
        trusted_proxies=trusted_proxies,
        auth_mode=auth_mode.lower(),
        auth_proxy_header=auth_proxy_header,
        log_retention_days=_parse_log_retention_days(log_retention_days),
    )

    # Validate authentication configuration before starting
    auth_errors = settings.validate_auth_config()
    if auth_errors:
        for err in auth_errors:
            logging.critical("Configuration error: %s", err)
        raise SystemExit(1)

    if settings.auth_mode == "proxy":
        logging.info("Auth mode: proxy (trusted proxies configured)")
    else:
        logging.info("Auth mode: builtin")

    # Propagate the remaining resolved CLI values to env vars so that
    # uvicorn's reload child process (which reimports modules fresh,
    # losing _runtime_settings) gets the correct values from
    # get_settings()'s env-var fallback. bootstrap_non_web already
    # exports HOUNDARR_DATA_DIR.
    os.environ["HOUNDARR_DEV"] = "1" if dev else ""
    os.environ["HOUNDARR_LOG_LEVEL"] = log_level.lower()
    os.environ["HOUNDARR_SECURE_COOKIES"] = "1" if secure_cookies else ""
    os.environ["HOUNDARR_COOKIE_SAMESITE"] = cookie_samesite.lower()
    os.environ["HOUNDARR_TRUSTED_PROXIES"] = trusted_proxies
    os.environ["HOUNDARR_AUTH_MODE"] = auth_mode.lower()
    os.environ["HOUNDARR_AUTH_PROXY_HEADER"] = auth_proxy_header
    os.environ["HOUNDARR_LOG_RETENTION_DAYS"] = log_retention_days

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

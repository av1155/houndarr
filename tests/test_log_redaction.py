"""The container log never prints an *arr URL's password."""

from __future__ import annotations

import io
import logging
import logging.config
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from click.testing import CliRunner
from uvicorn.config import LOGGING_CONFIG

from houndarr.__main__ import cli
from houndarr.config import AppSettings
from houndarr.log_redaction import ROOT_LOG_FORMAT, RedactingFormatter, uvicorn_log_config

URL = "http://user:hunter2@sonarr:8989/api/v3/series"
MASKED = "http://user:[secure]@sonarr:8989/api/v3/series"


def _record(msg: str, *args: object, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, exc_info)


def test_formatter_masks_httpx_request_line() -> None:
    line = RedactingFormatter(ROOT_LOG_FORMAT).format(
        _record('HTTP Request: %s %s "%s %d %s"', "GET", URL, "HTTP/1.1", 401, "Unauthorized")
    )

    assert "hunter2" not in line
    assert MASKED in line


def test_formatter_masks_a_traceback() -> None:
    try:
        raise RuntimeError(f"Client error '401 Unauthorized' for url '{URL}'")
    except RuntimeError as exc:
        line = RedactingFormatter(ROOT_LOG_FORMAT).format(_record("cycle failed", exc=exc))

    assert "Traceback" in line
    assert "hunter2" not in line
    assert MASKED in line


@pytest.fixture()
def restore_logging() -> Iterator[None]:
    """Put back the handlers, levels and propagation ``dictConfig`` replaces."""
    loggers = [logging.getLogger(n) for n in ("", "uvicorn", "uvicorn.error", "uvicorn.access")]
    saved = [(lg, lg.handlers[:], lg.level, lg.propagate) for lg in loggers]
    yield
    for logger, handlers, level, propagate in saved:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize("logger_name", ["houndarr.engine.supervisor", "httpx", "uvicorn.error"])
def test_uvicorn_log_config_masks_every_handler(
    logger_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root handler and uvicorn's own handler both mask, at the chosen level."""
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    logging.config.dictConfig(uvicorn_log_config("info"))

    logging.getLogger(logger_name).info("HTTP Request: GET %s", URL)
    logging.getLogger(logger_name).debug("below the level %s", URL)

    out = stream.getvalue()
    assert "hunter2" not in out
    assert f"HTTP Request: GET {MASKED}" in out
    assert "below the level" not in out


def test_uvicorn_log_config_leaves_uvicorns_default_untouched() -> None:
    before = repr(LOGGING_CONFIG)

    config = uvicorn_log_config("debug")

    assert repr(LOGGING_CONFIG) == before
    assert config["root"]["level"] == "DEBUG"
    assert set(config["formatters"]) >= {"default", "access"}


@pytest.mark.usefixtures("restore_logging")
def test_dev_reload_child_logs_houndarr_debug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under ``--dev`` only uvicorn configures the reload child's logging, from cli()'s config."""
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        captured.update(kwargs, app=app)

    def fake_bootstrap(data_dir: str, **overrides: Any) -> tuple[AppSettings, Path, bytes]:
        return AppSettings(data_dir=data_dir, **overrides), tmp_path / "houndarr.db", b""

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("houndarr.bootstrap.bootstrap_non_web", fake_bootstrap)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    args = ["--data-dir", str(tmp_path), "--dev", "--log-level", "debug"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    config = uvicorn.Config(**captured)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    # What uvicorn's spawned reload child runs before serving.
    config.configure_logging()

    assert root.handlers
    assert logging.getLogger("houndarr.engine.supervisor").isEnabledFor(logging.DEBUG)

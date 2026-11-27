"""Logging setup that keeps \\*arr URL passwords out of the container log.

An instance URL can carry ``user:password@`` for an \\*arr behind basic
auth.  Houndarr's own messages, ``httpx``'s per-request INFO line and
exception text all print that URL, so every handler formats through
:func:`~houndarr.errors.redact_url_credentials`, tracebacks included.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import DefaultFormatter

from houndarr.errors import redact_url_credentials

ROOT_LOG_FORMAT = "%(levelname)s:     %(message)s"


class RedactingFormatter(logging.Formatter):
    """A :class:`logging.Formatter` that masks URL passwords in the finished line."""

    def format(self, record: logging.LogRecord) -> str:
        """Format *record*, then mask any ``//user:password@`` in the result."""
        return redact_url_credentials(super().format(record))


class _RedactingDefaultFormatter(RedactingFormatter, DefaultFormatter):
    """uvicorn's own formatter, which prints the tracebacks of failed requests."""


def uvicorn_log_config(log_level: str) -> dict[str, Any]:
    """Return uvicorn's logging config with a redacting root handler added.

    uvicorn applies this in its own process and in each ``--dev`` reload
    child, which starts with no root handler of its own.
    """
    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["default"]["()"] = _RedactingDefaultFormatter
    config["formatters"]["houndarr"] = {"()": RedactingFormatter, "fmt": ROOT_LOG_FORMAT}
    config["handlers"]["houndarr"] = {
        "formatter": "houndarr",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
    }
    config["root"] = {"handlers": ["houndarr"], "level": log_level.upper()}
    return config

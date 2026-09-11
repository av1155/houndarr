"""Shared fixtures for the async engine-cycle e2e tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.conftest import empty_download_queue


@pytest.fixture(autouse=True)
def _empty_download_queue() -> Iterator[None]:
    """Serve an empty *arr download queue to every e2e cycle test."""
    yield from empty_download_queue()

"""Pin the narrow adapter fallback catches in ``fetch_upgrade_pool``.

Sonarr and Whisparr v2 iterate monitored series inside
``fetch_upgrade_pool`` and catch ``client.get_episodes(series_id)``
failures with ``except (httpx.HTTPError, httpx.InvalidURL,
ValidationError)``, so one series that fails to load does not stop the
pool.  Anything else (``KeyError``, ``RuntimeError``, etc.) propagates to
the search_loop wrap (:func:`_fetch_pool_with_typed_wrap`), which
converts it to :class:`~houndarr.errors.EnginePoolFetchError`.

Lidarr and Readarr catch nothing here: a cutoff page that fails ends the
exclusion read, so the pass defers or writes its error row instead of
searching cutoff-unmet items as upgrades.  Only the walk's own tie-split
re-read swallows a failure, keeping what the first read excluded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from houndarr.engine.adapters import lidarr, readarr, sonarr, whisparr_v2
from houndarr.services.instances import InstanceType
from tests.test_engine.conftest import make_instance

pytestmark = pytest.mark.pinning


def _series(series_id: int) -> MagicMock:
    """Shorthand for a monitored series with the given id."""
    obj = MagicMock()
    obj.id = series_id
    obj.monitored = True
    return obj


def _validation_error() -> ValidationError:
    """Build a real :class:`ValidationError` via a trivial Pydantic failure."""
    from pydantic import BaseModel

    class _M(BaseModel):
        x: int

    try:
        _M.model_validate({"x": "not-an-int"})
    except ValidationError as exc:
        return exc
    raise AssertionError("ValidationError factory failed to raise")


# Sonarr (series-rotation per-iteration catch)


class TestSonarrAdapterNarrowCatch:
    """Pin sonarr.fetch_upgrade_pool's per-series narrow catch."""

    @pytest.mark.asyncio()
    async def test_http_error_per_series_skips_and_continues(self) -> None:
        """httpx.ConnectError on get_episodes is swallowed; pool builds."""
        client = MagicMock()
        client.get_series = AsyncMock(return_value=[_series(1)])
        client.get_episodes = AsyncMock(side_effect=httpx.ConnectError("refused"))
        result = await sonarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.sonarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_validation_error_per_series_skips_and_continues(self) -> None:
        """pydantic.ValidationError on get_episodes is swallowed; pool builds."""
        client = MagicMock()
        client.get_series = AsyncMock(return_value=[_series(1)])
        client.get_episodes = AsyncMock(side_effect=_validation_error())
        result = await sonarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.sonarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_non_http_exception_propagates(self) -> None:
        """RuntimeError escapes so search_loop's pool wrap can type it."""
        client = MagicMock()
        client.get_series = AsyncMock(return_value=[_series(1)])
        client.get_episodes = AsyncMock(side_effect=RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            await sonarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.sonarr))


# Whisparr v2 (mirrors Sonarr: series rotation + per-series catch)


class TestWhisparrV2AdapterNarrowCatch:
    """Pin whisparr_v2.fetch_upgrade_pool's per-series narrow catch."""

    @pytest.mark.asyncio()
    async def test_http_error_per_series_skips_and_continues(self) -> None:
        """httpx.ReadTimeout on get_episodes is swallowed; pool builds."""
        client = MagicMock()
        client.get_series = AsyncMock(return_value=[_series(1)])
        client.get_episodes = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        result = await whisparr_v2.fetch_upgrade_pool(
            client, make_instance(itype=InstanceType.whisparr_v2)
        )
        assert result == []

    @pytest.mark.asyncio()
    async def test_validation_error_per_series_skips_and_continues(self) -> None:
        """pydantic.ValidationError on get_episodes is swallowed."""
        client = MagicMock()
        client.get_series = AsyncMock(return_value=[_series(1)])
        client.get_episodes = AsyncMock(side_effect=_validation_error())
        result = await whisparr_v2.fetch_upgrade_pool(
            client, make_instance(itype=InstanceType.whisparr_v2)
        )
        assert result == []

    @pytest.mark.asyncio()
    async def test_non_http_exception_propagates(self) -> None:
        """KeyError escapes so the outer wrap can type it."""
        client = MagicMock()
        client.get_series = AsyncMock(return_value=[_series(1)])
        client.get_episodes = AsyncMock(side_effect=KeyError("bug"))
        with pytest.raises(KeyError):
            await whisparr_v2.fetch_upgrade_pool(
                client, make_instance(itype=InstanceType.whisparr_v2)
            )


# Lidarr / Readarr (cutoff exclusion read)


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("module", "itype", "library"),
    [
        (lidarr, InstanceType.lidarr, "get_albums"),
        (readarr, InstanceType.readarr, "get_books"),
    ],
    ids=["lidarr", "readarr"],
)
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        _validation_error(),
        RuntimeError("bug"),
    ],
    ids=["connect", "read-timeout", "validation", "runtime"],
)
async def test_a_failed_cutoff_page_propagates_before_the_library_read(
    module: Any, itype: InstanceType, library: str, error: Exception
) -> None:
    """Every cutoff page failure escapes so the outer wrap can defer or report it."""
    client = MagicMock()
    client.get_wanted_page = AsyncMock(side_effect=error)
    setattr(client, library, AsyncMock(return_value=[]))
    with pytest.raises(type(error)):
        await module.fetch_upgrade_pool(client, make_instance(itype=itype))
    getattr(client, library).assert_not_awaited()


# Shape check: confirm the two fallback catches are indeed narrowed


@pytest.mark.asyncio()
async def test_narrow_catch_tuple_is_consistent_across_adapters() -> None:
    """Every narrowed except clause contains the same 3-class tuple.

    This is a weak structural check against future edits that drift
    the catch shape between adapters.  It reads the source of each
    adapter module and asserts the tuple token appears verbatim, once
    in each series-walking adapter and never in Lidarr or Readarr.
    """
    import pathlib

    src_root = pathlib.Path(sonarr.__file__).parent
    expected = "except (httpx.HTTPError, httpx.InvalidURL, ValidationError):"
    for adapter_name, catches in (
        ("sonarr.py", 1),
        ("whisparr_v2.py", 1),
        ("lidarr.py", 0),
        ("readarr.py", 0),
    ):
        source = (src_root / adapter_name).read_text()
        count = source.count(expected)
        assert count == catches, f"{adapter_name}: expected {catches} narrow catch, found {count}"


@pytest.mark.asyncio()
async def test_no_broad_bare_exception_left_in_adapters() -> None:
    """Four adapters must no longer carry ``except Exception  # noqa: BLE001``.

    Defensive: if a future edit reintroduces the broad catch in one of
    the four narrowed adapters, this assertion catches it.  Whisparr
    v3 is deliberately excluded: its fetch_upgrade_pool does not have
    a per-iteration catch today.
    """
    import pathlib

    src_root = pathlib.Path(sonarr.__file__).parent
    for adapter_name in ("sonarr.py", "lidarr.py", "readarr.py", "whisparr_v2.py"):
        source = (src_root / adapter_name).read_text()
        assert "except Exception" not in source, (
            f"{adapter_name} still contains a broad `except Exception` catch"
        )


# mypy-silencing: suppress the unused-variable hint on Any/Callable imports.
_UNUSED: Any = None

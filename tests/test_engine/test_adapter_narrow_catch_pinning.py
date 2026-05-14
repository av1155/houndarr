"""Pin the narrow adapter fallback catches in ``fetch_upgrade_pool``.

Each of Sonarr, Lidarr, Readarr, and Whisparr v2 has at least one
``try`` inside ``fetch_upgrade_pool`` that swallows transient client
failures so pool building can continue:

* Sonarr / Whisparr v2 iterate monitored series and catch
  ``client.get_episodes(series_id)`` failures (one narrow catch
  per adapter).
* Lidarr / Readarr have two narrow catches each: one around the
  cutoff-page loop (``client.get_cutoff_unmet``) that builds the
  exclusion set, and one around the library fetch
  (``client.get_albums`` / ``client.get_books``) that supplies the
  upgrade candidate pool.

Each catch is ``except (httpx.HTTPError, httpx.InvalidURL,
ValidationError)`` so pool building stays resilient to transient
network or wire-validation noise.  Anything else (``KeyError``,
``RuntimeError``, ``AttributeError``, etc.) propagates to the
search_loop wrap (:func:`_fetch_pool_with_typed_wrap`), which
converts it to :class:`~houndarr.errors.EnginePoolFetchError`.

These tests lock both sides of every narrow-catch contract per
adapter.
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


# Lidarr (cutoff-exclusion page loop)


class TestLidarrAdapterNarrowCatch:
    """Pin lidarr.fetch_upgrade_pool's cutoff-exclusion and library-fetch catches."""

    @pytest.mark.asyncio()
    async def test_http_error_on_cutoff_page_breaks_exclusion_loop(self) -> None:
        """httpx.ConnectError on get_cutoff_unmet is swallowed; exclusion stays empty."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client.get_albums = AsyncMock(return_value=[])
        result = await lidarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.lidarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_validation_error_on_cutoff_page_breaks_exclusion_loop(self) -> None:
        """pydantic.ValidationError on get_cutoff_unmet is swallowed."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(side_effect=_validation_error())
        client.get_albums = AsyncMock(return_value=[])
        result = await lidarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.lidarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_non_http_exception_propagates(self) -> None:
        """RuntimeError escapes so the outer wrap can type it."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(side_effect=RuntimeError("bug"))
        client.get_albums = AsyncMock(return_value=[])
        with pytest.raises(RuntimeError):
            await lidarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.lidarr))

    @pytest.mark.asyncio()
    async def test_http_error_on_library_fetch_returns_empty(self) -> None:
        """httpx.ConnectError on get_albums is swallowed; pool is empty."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(return_value=[])
        client.get_albums = AsyncMock(side_effect=httpx.ConnectError("refused"))
        result = await lidarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.lidarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_validation_error_on_library_fetch_returns_empty(self) -> None:
        """pydantic.ValidationError on get_albums is swallowed."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(return_value=[])
        client.get_albums = AsyncMock(side_effect=_validation_error())
        result = await lidarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.lidarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_non_http_exception_on_library_fetch_propagates(self) -> None:
        """RuntimeError on get_albums escapes so the outer wrap can type it."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(return_value=[])
        client.get_albums = AsyncMock(side_effect=RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            await lidarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.lidarr))


# Readarr (mirrors Lidarr)


class TestReadarrAdapterNarrowCatch:
    """Pin readarr.fetch_upgrade_pool's cutoff-exclusion and library-fetch catches."""

    @pytest.mark.asyncio()
    async def test_http_error_on_cutoff_page_breaks_exclusion_loop(self) -> None:
        """httpx.ReadTimeout on get_cutoff_unmet is swallowed; exclusion stays empty."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        client.get_books = AsyncMock(return_value=[])
        result = await readarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.readarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_validation_error_on_cutoff_page_breaks_exclusion_loop(self) -> None:
        """pydantic.ValidationError on get_cutoff_unmet is swallowed."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(side_effect=_validation_error())
        client.get_books = AsyncMock(return_value=[])
        result = await readarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.readarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_non_http_exception_propagates(self) -> None:
        """AttributeError escapes so the outer wrap can type it."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(side_effect=AttributeError("bug"))
        client.get_books = AsyncMock(return_value=[])
        with pytest.raises(AttributeError):
            await readarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.readarr))

    @pytest.mark.asyncio()
    async def test_http_error_on_library_fetch_returns_empty(self) -> None:
        """httpx.ReadTimeout on get_books is swallowed; pool is empty."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(return_value=[])
        client.get_books = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        result = await readarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.readarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_validation_error_on_library_fetch_returns_empty(self) -> None:
        """pydantic.ValidationError on get_books is swallowed."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(return_value=[])
        client.get_books = AsyncMock(side_effect=_validation_error())
        result = await readarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.readarr))
        assert result == []

    @pytest.mark.asyncio()
    async def test_non_http_exception_on_library_fetch_propagates(self) -> None:
        """AttributeError on get_books escapes so the outer wrap can type it."""
        client = MagicMock()
        client.get_cutoff_unmet = AsyncMock(return_value=[])
        client.get_books = AsyncMock(side_effect=AttributeError("bug"))
        with pytest.raises(AttributeError):
            await readarr.fetch_upgrade_pool(client, make_instance(itype=InstanceType.readarr))


# Shape check: confirm the four fallback catches are indeed narrowed


@pytest.mark.asyncio()
async def test_narrow_catch_tuple_is_consistent_across_adapters() -> None:
    """Every narrowed except clause contains the same 3-class tuple.

    This is a weak structural check against future edits that drift
    the catch shape between adapters.  It reads the source of each
    adapter module and asserts the tuple token appears verbatim, the
    expected number of times per adapter: once for the series-rotation
    adapters (Sonarr, Whisparr v2) and twice for the cutoff-exclusion
    adapters (Lidarr, Readarr), which guard both the cutoff page loop
    and the library fetch.
    """
    import pathlib

    src_root = pathlib.Path(sonarr.__file__).parent
    expected = "except (httpx.HTTPError, httpx.InvalidURL, ValidationError):"
    expected_counts = {
        "sonarr.py": 1,
        "lidarr.py": 2,
        "readarr.py": 2,
        "whisparr_v2.py": 1,
    }
    for adapter_name, expected_count in expected_counts.items():
        source = (src_root / adapter_name).read_text()
        count = source.count(expected)
        assert count == expected_count, (
            f"{adapter_name}: expected {expected_count} narrow catch(es), found {count}"
        )


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

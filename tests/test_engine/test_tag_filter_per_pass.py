"""Tag filters through full search cycles, pass by pass (issue #833)."""

from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx

from houndarr.engine.search_loop import run_instance_search
from houndarr.services.instances import Instance, InstanceType, TagFilterPolicy

from .conftest import MASTER_KEY, RADARR_URL, make_instance


def _radarr_all_passes(tag_filter: TagFilterPolicy) -> Instance:
    return dataclasses.replace(
        make_instance(
            instance_id=2,
            itype=InstanceType.radarr,
            cutoff_enabled=True,
            upgrade_enabled=True,
            upgrade_cooldown_days=7,
            upgrade_hourly_cap=5,
        ),
        tag_filter=tag_filter,
    )


def _serve_empty_radarr() -> dict[str, respx.Route]:
    """Serve empty wanted lists, an empty library and one tag."""
    return {
        "missing": respx.get(f"{RADARR_URL}/api/v3/wanted/missing").mock(
            return_value=httpx.Response(200, json={"records": []})
        ),
        "cutoff": respx.get(f"{RADARR_URL}/api/v3/wanted/cutoff").mock(
            return_value=httpx.Response(200, json={"records": []})
        ),
        "upgrade": respx.get(f"{RADARR_URL}/api/v3/movie").mock(
            return_value=httpx.Response(200, json=[])
        ),
        "tag": respx.get(f"{RADARR_URL}/api/v3/tag").mock(
            return_value=httpx.Response(200, json=[{"id": 3, "label": "skip"}])
        ),
    }


@pytest.mark.pinning()
@pytest.mark.asyncio()
@respx.mock
async def test_no_tag_get_without_labels_on_any_pass(seeded_instances: None) -> None:
    """An instance with no tag labels never requests ``/tag``."""
    routes = _serve_empty_radarr()

    await run_instance_search(_radarr_all_passes(TagFilterPolicy()), MASTER_KEY)

    assert routes["missing"].called
    assert routes["cutoff"].called
    assert routes["upgrade"].called
    assert not routes["tag"].called


@pytest.mark.pinning()
@pytest.mark.asyncio()
@respx.mock
async def test_tag_get_once_per_cycle_instance_wide_labels(seeded_instances: None) -> None:
    """Instance-wide labels cost one ``/tag`` request per cycle, whatever the pass count."""
    routes = _serve_empty_radarr()

    await run_instance_search(
        _radarr_all_passes(TagFilterPolicy(include=(), exclude=("skip",))), MASTER_KEY
    )

    assert routes["missing"].called
    assert routes["cutoff"].called
    assert routes["upgrade"].called
    assert routes["tag"].call_count == 1

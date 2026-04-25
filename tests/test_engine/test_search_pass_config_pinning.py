"""Pin the :class:`SearchPassConfig` dataclass shape.

Locks the fields, defaults, and invariants every consumer of the
config relies on so a field rename or default flip fails here
loudly.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable
from typing import Any

import pytest

from houndarr.engine.candidates import SearchCandidate
from houndarr.engine.config.search_pass import SearchPassConfig
from houndarr.enums import CycleTrigger, ItemType, SearchKind

pytestmark = pytest.mark.pinning


def _adapt(item: Any, instance: Any) -> SearchCandidate:
    return SearchCandidate(
        item_id=0,
        item_type=ItemType.episode,
        label="",
        unreleased_reason=None,
        group_key=None,
        search_payload={},
    )


async def _dispatch(client: Any, candidate: Any) -> None:
    return None


async def _fetch(**kwargs: Any) -> list[Any]:
    return []


async def _total() -> int:
    return 0


class TestSearchPassConfigDeclaration:
    """Pin the dataclass shape so consumers can read ``config.foo`` safely."""

    def test_is_frozen(self) -> None:
        """``frozen=True`` prevents in-place mutation at the pass boundary."""
        config = SearchPassConfig(
            search_kind=SearchKind.missing,
            adapt_fn=_adapt,
            dispatch_fn=_dispatch,
            fetch_fn=_fetch,
            batch_size=5,
            hourly_cap=10,
            cooldown_days=7,
            page_size=20,
            scan_budget=40,
            cycle_id="cid",
            cycle_trigger=CycleTrigger.scheduled,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.batch_size = 99  # type: ignore[misc]

    def test_slots_means_no_instance_dict(self) -> None:
        """``slots=True`` strips ``__dict__`` so memory footprint stays tight.

        frozen=True already rejects in-place mutation; slots=True adds
        the memory-footprint discipline every Houndarr value object
        shares.  The absence of ``__dict__`` is the clearest marker.
        """
        config = SearchPassConfig(
            search_kind=SearchKind.cutoff,
            adapt_fn=_adapt,
            dispatch_fn=_dispatch,
            fetch_fn=_fetch,
            batch_size=5,
            hourly_cap=0,
            cooldown_days=21,
            page_size=10,
            scan_budget=12,
            cycle_id="cid-2",
            cycle_trigger=CycleTrigger.run_now,
        )
        assert not hasattr(config, "__dict__")

    def test_start_page_default_is_one(self) -> None:
        """The default start page matches the 1-based convention in search_log."""
        config = SearchPassConfig(
            search_kind=SearchKind.missing,
            adapt_fn=_adapt,
            dispatch_fn=_dispatch,
            fetch_fn=_fetch,
            batch_size=1,
            hourly_cap=0,
            cooldown_days=0,
            page_size=1,
            scan_budget=1,
            cycle_id="c",
            cycle_trigger=CycleTrigger.scheduled,
        )
        assert config.start_page == 1

    def test_total_fn_default_is_none(self) -> None:
        """Omitting ``total_fn`` disables the random probe fallback."""
        config = SearchPassConfig(
            search_kind=SearchKind.cutoff,
            adapt_fn=_adapt,
            dispatch_fn=_dispatch,
            fetch_fn=_fetch,
            batch_size=1,
            hourly_cap=0,
            cooldown_days=0,
            page_size=1,
            scan_budget=1,
            cycle_id="c",
            cycle_trigger=CycleTrigger.system,
        )
        assert config.total_fn is None

    def test_field_order_matches_current_kwargs(self) -> None:
        """Field order is the same as ``_run_search_pass``'s kwarg order.

        Call sites read ``config.<field>`` so an accidental reorder
        would change default-value semantics; locking the order here
        catches it.
        """
        assert [f.name for f in dataclasses.fields(SearchPassConfig)] == [
            "search_kind",
            "adapt_fn",
            "dispatch_fn",
            "fetch_fn",
            "batch_size",
            "hourly_cap",
            "cooldown_days",
            "page_size",
            "scan_budget",
            "cycle_id",
            "cycle_trigger",
            "start_page",
            "total_fn",
        ]

    def test_optional_total_fn_accepts_bound_coroutine(self) -> None:
        """``total_fn`` stores the caller's bound coroutine untouched."""
        config = SearchPassConfig(
            search_kind=SearchKind.missing,
            adapt_fn=_adapt,
            dispatch_fn=_dispatch,
            fetch_fn=_fetch,
            batch_size=2,
            hourly_cap=10,
            cooldown_days=7,
            page_size=5,
            scan_budget=10,
            cycle_id="c",
            cycle_trigger=CycleTrigger.scheduled,
            total_fn=_total,
        )
        assert config.total_fn is _total


# Pure type-compat exercise: ensure Awaitable-returning callables type-check.
def _type_check_example(config: SearchPassConfig) -> Awaitable[None]:
    return config.dispatch_fn(object(), object())

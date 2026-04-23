"""Frozen value object for one missing or cutoff search pass.

:func:`houndarr.engine.search_loop._run_search_pass` takes 13 values
that fall naturally into four groups: pass identity
(``search_kind``), adapter bindings (``adapt_fn``, ``dispatch_fn``,
``fetch_fn``, ``total_fn``), behaviour knobs (``batch_size``,
``hourly_cap``, ``cooldown_days``, ``page_size``, ``scan_budget``),
and cycle metadata (``cycle_id``, ``cycle_trigger``,
``start_page``).  :class:`SearchPassConfig` collapses them into a
single frozen dataclass so
:func:`~houndarr.engine.search_loop.run_instance_search` passes one
value instead of keyword-unpacking every field.

The dataclass is frozen + slots for the same reason every other
value object in Houndarr is: keep construction cheap, reject
accidental mutation across the per-cycle boundary, and let the type
checker narrow safely.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from houndarr.engine.candidates import SearchCandidate
from houndarr.enums import CycleTrigger, SearchKind


@dataclass(frozen=True, slots=True)
class SearchPassConfig:
    """Per-pass configuration for :func:`_run_search_pass`.

    The fields mirror the keyword-argument surface one-for-one so
    call sites read ``config.foo`` where they used to pass
    ``foo=bar``.

    Attributes:
        search_kind: ``"missing"`` or ``"cutoff"``.
        adapt_fn: Converts a raw *arr item into a
            :class:`SearchCandidate`.  Typically bound to the
            adapter's ``adapt_missing`` or ``adapt_cutoff``.
        dispatch_fn: Sends the *arr search command for one candidate.
            Typically ``adapter.dispatch_search``.
        fetch_fn: Returns one page of wanted items.  Typically
            ``client.get_missing`` or ``client.get_cutoff_unmet``.
        batch_size: Maximum items to search in this pass.
        hourly_cap: Hourly search cap for this pass kind
            (``0`` disables the cap).
        cooldown_days: Days since last search after which an item is
            eligible again.
        page_size: Items to request per page from *arr.
        scan_budget: Upper bound on candidates evaluated before the
            pass aborts (guards against scanning past the end of the
            list when everything is on cooldown).
        cycle_id: Shared identifier written to every ``search_log``
            row produced by the pass.
        cycle_trigger: ``"scheduled"``, ``"run_now"``, or ``"system"``.
        start_page: 1-based first page to fetch.  Rotates across
            cycles under chronological search order.
        total_fn: Optional probe returning the total wanted-item count.
            Used by random search order to pick a random start page.
            ``None`` disables the random probe and falls back to
            *start_page*.
    """

    search_kind: SearchKind | str
    adapt_fn: Callable[..., SearchCandidate]
    dispatch_fn: Callable[..., Awaitable[None]]
    fetch_fn: Callable[..., Awaitable[list[Any]]]
    batch_size: int
    hourly_cap: int
    cooldown_days: int
    page_size: int
    scan_budget: int
    cycle_id: str
    cycle_trigger: CycleTrigger | str
    start_page: int = 1
    total_fn: Callable[[], Awaitable[int]] | None = None


__all__ = ["SearchPassConfig"]

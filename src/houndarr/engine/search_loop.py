"""Per-instance search loop.

:func:`run_instance_search` is the single entry point called by the supervisor.
It fetches one batch of missing items, applies cooldown and hourly-cap checks,
triggers the *arr search command for each eligible item, and writes a row to
``search_log`` for every item processed.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from houndarr.engine.adapters import get_adapter
from houndarr.engine.adapters.protocols import AppAdapterProto
from houndarr.engine.candidates import SearchCandidate
from houndarr.engine.config.search_pass import SearchPassConfig
from houndarr.enums import CycleTrigger, ItemType, SearchAction, SearchKind
from houndarr.errors import (
    ClientError,
    EngineDispatchError,
    EngineError,
    EngineOffsetPersistError,
    EnginePoolFetchError,
)
from houndarr.services.cooldown import (
    is_on_cooldown_ref,
    record_search_ref,
    should_log_skip,
)
from houndarr.services.instances import Instance, InstanceType, SearchOrder, update_instance
from houndarr.services.time_window import (
    format_ranges,
    is_within_window,
    parse_time_window,
)
from houndarr.value_objects import ItemRef

logger = logging.getLogger(__name__)

_MAX_LIST_PAGES_PER_PASS = 5
_MISSING_PAGE_SIZE_MIN = 10
_MISSING_PAGE_SIZE_MAX = 50
_MISSING_SCAN_BUDGET_MIN = 24
_MISSING_SCAN_BUDGET_MAX = 120
_CUTOFF_PAGE_SIZE_MIN = 5
_CUTOFF_PAGE_SIZE_MAX = 25
_CUTOFF_SCAN_BUDGET_MIN = 12
_CUTOFF_SCAN_BUDGET_MAX = 60

# Delay inserted between consecutive real searches within one cycle to spread
# downstream indexer fan-out.  Each *arr search command causes the arr app to
# query every configured indexer simultaneously; back-to-back searches compound
# that burst.  A short pause keeps Houndarr polite without changing its
# sequential architecture.  Zero this in tests via patch.object.
_INTER_SEARCH_DELAY_SECONDS: float = 3.0

# Upgrade pass hard caps (very conservative, items already have files)
_UPGRADE_SCAN_BUDGET_MIN = 8
_UPGRADE_SCAN_BUDGET_MAX = 40
_UPGRADE_BATCH_HARD_CAP = 5
_UPGRADE_HOURLY_CAP_HARD_CAP = 5
_UPGRADE_MIN_COOLDOWN_DAYS = 7


# ---------------------------------------------------------------------------
# Random search order: stratified-shuffle deck
# ---------------------------------------------------------------------------
#
# When ``SearchOrder.random`` is active, ``_run_search_pass`` picks page
# numbers from a shuffled permutation of ``[1, max_page]`` rather than a
# fresh ``random.randint`` each cycle plus a forward walk with wrap-once.
# The deck is held in this module-level cache, keyed by
# ``(instance_id, search_kind)`` so each (instance, missing/cutoff) pair
# tracks its own progress through the current round.
#
# Two properties this design buys over the old algorithm:
#
#   1. Bounded short-term variance.  Every page is visited exactly once
#      per round (one round == ``max_page`` page-draws). The worst-case
#      wait for a given page is therefore ``ceil(max_page / K)`` cycles,
#      where K = ``_MAX_LIST_PAGES_PER_PASS``.  The previous algorithm
#      could skip a page for arbitrarily many cycles in a row by chance.
#
#   2. No wrap-once asymmetry.  The original walk visited
#      ``{start, start+1, ..., max_page, 1, 2, ...}`` which favoured
#      pages near the end of the list when start>1 (a 1.25x-1.5x bias
#      under multi-page-walk conditions; see AGENTS.md "Verifying
#      Claims About Algorithms").  Pulling from a fresh shuffle has
#      no positional asymmetry.
#
# The deck is rebuilt whenever ``max_page`` changes (the wanted-list
# grew or shrank since the last cycle) or whenever the previous round
# is exhausted.  Stale entries for deleted instances stay in the dict
# but cost only a small list of integers per (instance, kind) pair.


@dataclass(slots=True)
class _RandomDeckState:
    """Per-(instance, kind) deck of remaining page numbers for random mode."""

    max_page: int
    remaining: list[int]


_random_decks: dict[tuple[int, str], _RandomDeckState] = {}


def _draw_next_random_page(instance_id: int, search_kind: SearchKind | str, max_page: int) -> int:
    """Pop the next page from this (instance, kind)'s shuffled deck.

    Refreshes the deck when it is empty or when ``max_page`` differs from
    the value the deck was built against (which happens when the user has
    added or removed wanted items in the *arr instance since the last
    cycle).
    """
    key = (instance_id, str(search_kind))
    state = _random_decks.get(key)
    if state is None or state.max_page != max_page or not state.remaining:
        deck = list(range(1, max_page + 1))
        random.shuffle(deck)  # noqa: S311  # nosec B311
        state = _RandomDeckState(max_page=max_page, remaining=deck)
        _random_decks[key] = state
    return state.remaining.pop()


def _reset_random_deck(instance_id: int, search_kind: SearchKind | str) -> None:
    """Drop the cached deck for one (instance, kind) pair.

    Test helper.  Production code does not need to call this because the
    deck self-refreshes on ``max_page`` mismatch and on exhaustion.
    """
    _random_decks.pop((instance_id, str(search_kind)), None)


# Sentinel used to pad partial last pages out to ``page_size`` in random mode
# so the within-page selection probability is the same on partial and full
# pages.  Without this padding, items on a short last page would be drained
# every visit (``actual_items < page_size`` means each item has higher than
# ``batch_size / page_size`` per-visit dispatch probability), accumulating a
# small but persistent over-selection of the last 1-9 items in any backlog
# whose ``totalRecords`` is not a multiple of ``page_size``.  Identity
# comparison (``item is _SHUFFLE_PAD``) is the cheap and unambiguous gate.
_SHUFFLE_PAD: object = object()


# search_log helpers


def _format_hourly_limit_reason(kind: SearchKind | str, cap: int) -> str:
    """Return the skip-reason string for a cap-exhausted pass.

    Centralises the phrasing used at the three hourly-cap gate sites
    (missing / cutoff / upgrade) so they cannot drift apart.  The
    parameter shape ``(N/hr)`` reads as "N per hour" to a user, where
    the older ``(N)`` form read as an error code — a repeat finding
    in post-Huntarr self-hoster research.
    """
    prefix = "" if kind == "missing" else f"{kind} "
    return f"{prefix}hourly limit reached ({cap}/hr)"


async def _write_log(
    instance_id: int | None,
    item_id: int | None,
    item_type: str | None,
    action: str,
    search_kind: SearchKind | str | None = None,
    cycle_id: str | None = None,
    cycle_trigger: CycleTrigger | str | None = None,
    item_label: str | None = None,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    """Insert a single row into ``search_log``.

    Thin delegator over
    :func:`houndarr.repositories.search_log.insert_log_row`.  The
    engine keeps this module-local symbol so the many hot-path call
    sites (queue gate, window gate, dispatch, cycle prologue / epilogue,
    upgrade pool fetch) all continue to import from one place; D.27
    will sweep remaining ``_write_log`` call sites to the repository
    directly.  The :class:`~houndarr.enums.SearchKind` /
    :class:`~houndarr.enums.CycleTrigger` unions collapse to their
    underlying str here so the repository sees a plain column value.
    """
    from houndarr.repositories.search_log import insert_log_row

    await insert_log_row(
        instance_id=instance_id,
        item_id=item_id,
        item_type=item_type,
        action=action,
        search_kind=str(search_kind) if search_kind is not None else None,
        cycle_id=cycle_id,
        cycle_trigger=str(cycle_trigger) if cycle_trigger is not None else None,
        item_label=item_label,
        reason=reason,
        message=message,
    )


async def _write_item_log(
    ref: ItemRef,
    action: str,
    *,
    search_kind: SearchKind | str | None = None,
    cycle_id: str | None = None,
    cycle_trigger: CycleTrigger | str | None = None,
    item_label: str | None = None,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    """Write one ``search_log`` row tied to a specific item reference.

    Thin wrapper over :func:`_write_log` that accepts :class:`ItemRef`
    in place of the three positional ``(instance_id, item_id, item_type)``
    arguments.  Used by the hot loops in :func:`_run_search_pass` and
    :func:`_run_upgrade_pass` where every persisted row is tied to an
    identified candidate.  Cycle-scope info / error rows (queue gate,
    window gate, upgrade pool fetch failure, upgrade pool empty) have no
    ``ItemRef`` and continue to call :func:`_write_log` directly with
    ``None`` for ``item_id`` and ``item_type``.

    Args:
        ref: The item this log row pertains to.
        action: One of the :class:`SearchAction` values (as ``str``).
        search_kind: ``"missing"`` / ``"cutoff"`` / ``"upgrade"``.
        cycle_id: Shared cycle identifier for all rows in one cycle.
        cycle_trigger: ``"scheduled"`` / ``"run_now"`` / ``"system"``.
        item_label: Human-readable label for the item.
        reason: Structured skip reason for ``skipped`` rows.
        message: Free-form detail for ``error`` / ``info`` rows.
    """
    await _write_log(
        ref.instance_id,
        ref.item_id,
        ref.item_type.value,
        action,
        search_kind=search_kind,
        cycle_id=cycle_id,
        cycle_trigger=cycle_trigger,
        item_label=item_label,
        reason=reason,
        message=message,
    )


def _clamp(value: int, minimum: int, maximum: int) -> int:
    """Clamp *value* to the [minimum, maximum] range."""
    return max(minimum, min(value, maximum))


def _missing_page_size(batch_size: int) -> int:
    """Return list page size for the missing pass."""
    return _clamp(batch_size * 4, _MISSING_PAGE_SIZE_MIN, _MISSING_PAGE_SIZE_MAX)


def _cutoff_page_size(batch_size: int) -> int:
    """Return list page size for the cutoff pass."""
    return _clamp(batch_size * 4, _CUTOFF_PAGE_SIZE_MIN, _CUTOFF_PAGE_SIZE_MAX)


def _missing_scan_budget(batch_size: int) -> int:
    """Return max candidates to evaluate during one missing pass."""
    return _clamp(batch_size * 12, _MISSING_SCAN_BUDGET_MIN, _MISSING_SCAN_BUDGET_MAX)


def _cutoff_scan_budget(batch_size: int) -> int:
    """Return max candidates to evaluate during one cutoff pass."""
    return _clamp(batch_size * 12, _CUTOFF_SCAN_BUDGET_MIN, _CUTOFF_SCAN_BUDGET_MAX)


async def _count_searches_last_hour(instance_id: int, search_kind: SearchKind | str) -> int:
    """Count successful searches in the last hour for one pass kind.

    Thin delegator over
    :func:`houndarr.repositories.search_log.fetch_recent_searches`
    since D.27.  The hourly cap only ever passes a 3600-second window;
    the repository surface takes the window as a parameter so the
    engine can stay on that single contract.
    """
    from houndarr.repositories.search_log import fetch_recent_searches

    return await fetch_recent_searches(
        instance_id,
        search_kind=str(search_kind),
        within_seconds=3600,
    )


async def _latest_missing_reason_ref(ref: ItemRef) -> str | None:
    """Return the latest logged missing-pass reason for *ref*, if any.

    Thin delegator over
    :func:`houndarr.repositories.search_log.fetch_latest_missing_reason`
    since D.27.  Used by the release-timing retry branch in
    :func:`_run_search_pass` to decide whether an item on cooldown
    should be retried (because the last logged reason was a
    pre-release or post-release-grace skip that has since elapsed).
    """
    from houndarr.repositories.search_log import fetch_latest_missing_reason

    return await fetch_latest_missing_reason(
        ref.instance_id,
        ref.item_id,
        ref.item_type.value,
    )


def _is_release_timing_reason(reason: str | None) -> bool:
    """Return ``True`` when *reason* indicates a release-timing block."""
    return reason == "not yet released" or (
        reason is not None and reason.startswith("post-release grace")
    )


# Typed wrap helpers for adapter calls


async def _dispatch_with_typed_wrap(
    adapter: AppAdapterProto,
    instance: Instance,
    dispatch_fn: Callable[..., Awaitable[None]],
    candidate: SearchCandidate,
) -> None:
    """Open a client, call *dispatch_fn*, and surface failures typed.

    The three dispatch call sites in :func:`_run_search_pass` and
    :func:`_run_upgrade_pass` share this helper so each one narrows
    its ``except Exception`` to :class:`EngineDispatchError`.  The
    helper owns the whole ``adapter.make_client -> dispatch`` attempt
    boundary: client construction (``httpx.InvalidURL``),
    context-manager entry and exit, and the dispatch call itself all
    get typed into one surface.

    Already-typed :class:`EngineError` and :class:`ClientError`
    subclasses propagate unchanged so richer context from the client
    layer is not flattened.

    The typed error message is ``str(exc)`` verbatim, which keeps
    the ``search_log.message`` field stable against the golden-log
    characterisation test.

    Args:
        adapter: :class:`AppAdapterProto` for the instance.
        instance: Fully-populated instance.
        dispatch_fn: Adapter dispatch callable; takes ``(client,
            candidate)`` and sends the search command.
        candidate: Item to dispatch.

    Raises:
        EngineDispatchError: Any non-typed ``Exception``; the original
            is preserved on ``__cause__``.
    """
    try:
        async with adapter.make_client(instance) as client:
            await dispatch_fn(client, candidate)
    except (EngineError, ClientError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineDispatchError(str(exc)) from exc


async def _persist_offset_with_typed_wrap(
    instance_id: int,
    *,
    master_key: bytes,
    **offsets: int,
) -> None:
    """Call :func:`update_instance` with offset kwargs, typing failures.

    The four offset-persist call sites (upgrade series offset and
    upgrade item offset in :func:`_run_upgrade_pass`; missing page
    offset and cutoff page offset in
    :func:`_run_instance_search_impl`) share this helper so each one
    narrows its ``except Exception`` to
    :class:`EngineOffsetPersistError`.

    These writes are non-fatal by design: callers swallow the typed
    error and the next cycle retries the persist.  The wrap keeps the
    log line identical (``"failed to persist ..."``) while giving
    observability a typed surface to key on.

    Args:
        instance_id: Primary key of the row being updated.
        master_key: Fernet key required by :func:`update_instance`.
        **offsets: Integer columns to update, e.g.
            ``missing_page_offset=7``.  Non-integer columns are not a
            valid shape for this helper; callers should use
            :func:`update_instance` directly for those.

    Raises:
        EngineOffsetPersistError: Any non-typed ``Exception``; the
            original is preserved on ``__cause__``.
    """
    try:
        await update_instance(instance_id, master_key=master_key, **offsets)
    except (EngineError, ClientError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineOffsetPersistError(str(exc)) from exc


async def _fetch_pool_with_typed_wrap(
    adapter: AppAdapterProto,
    instance: Instance,
) -> list[Any]:
    """Open a client, call ``adapter.fetch_upgrade_pool``, surface typed.

    Sibling of :func:`_dispatch_with_typed_wrap` for the upgrade-pool
    build path.  Owns the ``adapter.make_client -> fetch`` boundary so
    construction + context entry + pool fetch all land in the same
    :class:`EnginePoolFetchError` surface.

    Args:
        adapter: :class:`AppAdapterProto` for the instance.
        instance: Fully-populated instance.

    Returns:
        The raw upgrade-pool list produced by the adapter.

    Raises:
        EnginePoolFetchError: Any non-typed ``Exception``; the original
            is preserved on ``__cause__``.
    """
    try:
        async with adapter.make_client(instance) as client:
            return await adapter.fetch_upgrade_pool(client, instance)
    except (EngineError, ClientError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise EnginePoolFetchError(str(exc)) from exc


# Unified search pass


async def _run_search_pass(  # noqa: C901
    instance: Instance,
    adapter: AppAdapterProto,
    config: SearchPassConfig,
) -> tuple[int, int]:
    """Execute a single search pass (missing or cutoff) using the adapter.

    This is the unified pipeline that replaces the previously duplicated
    missing-pass inline code and the bifurcated ``_run_cutoff_pass()``
    function.  It pages through items, converts each to a
    :class:`SearchCandidate` via ``config.adapt_fn``, applies eligibility
    checks (unreleased delay, hourly cap, cooldown), and dispatches
    searches via ``config.dispatch_fn``.

    Args:
        instance: Fully-populated (decrypted) instance.
        adapter: The :class:`AppAdapterProto` implementation for this
            instance type.
        config: :class:`SearchPassConfig` carrying the adapter bindings,
            rate-shape knobs, cycle metadata, and pagination / total
            probe for this pass.  See the class docstring for the
            per-field contract.  Random search order uses
            ``config.total_fn`` to pick a random start page each cycle;
            probe failure falls back to ``config.start_page`` with a
            warning.

    Returns:
        Tuple of (items_searched, next_start_page).
    """
    adapt_fn = config.adapt_fn
    dispatch_fn = config.dispatch_fn
    fetch_fn = config.fetch_fn
    search_kind = config.search_kind
    batch_size = config.batch_size
    hourly_cap = config.hourly_cap
    cooldown_days = config.cooldown_days
    page_size = config.page_size
    scan_budget = config.scan_budget
    cycle_id = config.cycle_id
    cycle_trigger = config.cycle_trigger
    start_page = config.start_page
    total_fn = config.total_fn
    target = max(0, batch_size)
    if target == 0:
        return 0, start_page

    is_cutoff = search_kind == "cutoff"
    log_prefix = "cutoff " if is_cutoff else ""

    searches_this_hour = await _count_searches_last_hour(instance.core.id, search_kind)
    seen_item_ids: set[int] = set()
    seen_group_keys: set[tuple[int, int]] = set()
    searched = 0
    scanned = 0
    page = max(1, start_page)
    wrapped = False
    random_max_page = 0
    use_random_deck = False

    if instance.schedule.search_order == SearchOrder.random and total_fn is not None:
        try:
            total = await total_fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] %stotal probe failed (%s); falling back to page %d",
                instance.core.name,
                log_prefix,
                exc,
                page,
            )
        else:
            if total > 0:
                random_max_page = max(1, math.ceil(total / page_size))
                use_random_deck = True
                page = _draw_next_random_page(instance.core.id, search_kind, random_max_page)

    for _ in range(_MAX_LIST_PAGES_PER_PASS):
        if searched >= target or scanned >= scan_budget:
            break

        items = await fetch_fn(page=page, page_size=page_size)
        # Pad partial pages out to page_size with sentinels in random mode so
        # the per-item probability of being in the first batch_size positions
        # of the shuffle is exactly batch_size / page_size on every page.  We
        # only pad when there is more than one page in the wanted-list: with
        # max_page == 1 the partial page is the entire backlog and there is
        # no other page to compete with, so padding would only reduce
        # throughput without affecting fairness.  See the cooldown probe and
        # the AGENTS.md "Verifying Claims About Algorithms" section for the
        # bias derivation this guards against.
        pad_partial = use_random_deck and random_max_page > 1 and items and len(items) < page_size
        if pad_partial:
            items = list(items)
            while len(items) < page_size:
                items.append(_SHUFFLE_PAD)
        if instance.schedule.search_order == SearchOrder.random and items:
            random.shuffle(items)  # noqa: S311  # nosec B311
        logger.debug(
            "[%s] fetched %d %sitem(s) from page %d",
            instance.core.name,
            len(items),
            "cutoff-unmet " if is_cutoff else "missing ",
            page,
        )
        if not items:
            # Random mode draws every page from a pre-shuffled deck of
            # ``[1, max_page]``, so an empty response means the deck is
            # carrying a stale page number from before the wanted-list
            # shrank.  Skip to the next deck entry; the deck will refresh
            # itself when exhausted or when ``max_page`` no longer matches.
            if use_random_deck:
                page = _draw_next_random_page(instance.core.id, search_kind, random_max_page)
                continue
            # Chronological mode: paged past available data; wrap to page
            # 1 once per pass so items at the start of the list (which may
            # have come off cooldown) still get evaluated.
            if start_page > 1 and not wrapped:
                page = 1
                wrapped = True
                continue
            break

        stop_pass = False
        page_fully_consumed = True
        positions_consumed = 0
        # In random mode each page visit consumes exactly ``batch_size``
        # shuffled positions: padding sentinels count toward the position
        # quota but produce no dispatch, so partial pages dispatch fewer
        # real items per visit, equalising per-item attention across the
        # backlog.  Hitting the cap is treated as "page work done" so the
        # outer loop advances to the next deck page instead of re-fetching
        # this one.  In chronological mode there is no per-page position
        # cap; the existing ``searched >= target`` gate is the only break.
        position_cap = batch_size if pad_partial else None
        for item in items:
            if searched >= target or scanned >= scan_budget:
                page_fully_consumed = False
                break
            if position_cap is not None and positions_consumed >= position_cap:
                break
            positions_consumed += 1

            if item is _SHUFFLE_PAD:
                # Sentinel from partial-page padding: no real item to dispatch.
                # Counts as scanned (consumes scan budget) so the engine still
                # bounds its outbound work, but contributes zero dispatches.
                scanned += 1
                continue

            candidate = adapt_fn(item, instance)
            ref = ItemRef(
                instance.core.id,
                candidate.item_id,
                ItemType(candidate.item_type),
            )

            # Item-level modes can dedup immediately. Context modes defer dedup
            # until after release-timing checks so a temporarily blocked record
            # does not hide a later eligible record from the same group.
            if candidate.group_key is None:
                if candidate.item_id in seen_item_ids:
                    continue
                seen_item_ids.add(candidate.item_id)

            # Unreleased / post-release grace checks.
            # Pre-release gate is unconditional; post-release grace is
            # bypassed by run_now so the user's manual intent is respected.
            if candidate.unreleased_reason is not None:
                is_grace = candidate.unreleased_reason.startswith("post-release grace")
                if not (is_grace and cycle_trigger == "run_now"):
                    await _write_item_log(
                        ref,
                        SearchAction.skipped.value,
                        search_kind=search_kind,
                        cycle_id=cycle_id,
                        cycle_trigger=cycle_trigger,
                        item_label=candidate.label,
                        reason=candidate.unreleased_reason,
                    )
                    continue

            # Context-mode dedup happens after release-timing checks so a later
            # eligible record in the same season/artist/author can still drive
            # the group search when an earlier record was temporarily blocked.
            if candidate.group_key is not None:
                if candidate.group_key in seen_group_keys:
                    continue
                seen_group_keys.add(candidate.group_key)

                if candidate.item_id in seen_item_ids:
                    continue
                seen_item_ids.add(candidate.item_id)

            # Hourly cap.
            if hourly_cap > 0 and searches_this_hour >= hourly_cap:
                reason = _format_hourly_limit_reason(
                    "cutoff" if is_cutoff else "missing", hourly_cap
                )
                logger.info(
                    "[%s] %s%s: %s", instance.core.name, log_prefix, candidate.item_id, reason
                )
                await _write_item_log(
                    ref,
                    SearchAction.skipped.value,
                    search_kind=search_kind,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    reason=reason,
                )
                stop_pass = True
                break

            # Cooldown.
            if await is_on_cooldown_ref(ref, cooldown_days):
                if search_kind == "missing":
                    latest_reason = await _latest_missing_reason_ref(ref)
                    if _is_release_timing_reason(latest_reason):
                        logger.info(
                            "[%s] allowing missing retry for %s after release-timing block",
                            instance.core.name,
                            candidate.item_id,
                        )
                        scanned += 1
                        try:
                            await _dispatch_with_typed_wrap(
                                adapter, instance, dispatch_fn, candidate
                            )
                        except EngineDispatchError as exc:
                            msg = str(exc)
                            logger.warning(
                                "[%s] %ssearch failed for %s: %s",
                                instance.core.name,
                                log_prefix,
                                candidate.item_id,
                                msg,
                            )
                            await _write_item_log(
                                ref,
                                SearchAction.error.value,
                                search_kind=search_kind,
                                cycle_id=cycle_id,
                                cycle_trigger=cycle_trigger,
                                item_label=candidate.label,
                                message=msg,
                            )
                            continue

                        await record_search_ref(ref, search_kind)
                        await _write_item_log(
                            ref,
                            SearchAction.searched.value,
                            search_kind=search_kind,
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=candidate.label,
                        )
                        searched += 1
                        searches_this_hour += 1
                        logger.info(
                            "[%s] %ssearched %s %s",
                            instance.core.name,
                            log_prefix,
                            candidate.item_type,
                            candidate.item_id,
                        )
                        await asyncio.sleep(_INTER_SEARCH_DELAY_SECONDS)
                        continue

                reason = (
                    f"on cutoff cooldown ({cooldown_days}d)"
                    if is_cutoff
                    else f"on cooldown ({cooldown_days}d)"
                )
                bucket = "cutoff_cd" if is_cutoff else "cooldown"
                skip_key = (instance.core.id, candidate.item_id, search_kind, bucket)
                if cycle_trigger == "run_now" or await should_log_skip(skip_key):
                    logger.debug(
                        "[%s] %s%s: %s",
                        instance.core.name,
                        log_prefix,
                        candidate.item_id,
                        reason,
                    )
                    await _write_item_log(
                        ref,
                        SearchAction.skipped.value,
                        search_kind=search_kind,
                        cycle_id=cycle_id,
                        cycle_trigger=cycle_trigger,
                        item_label=candidate.label,
                        reason=reason,
                    )
                continue

            # Count only eligible (non-skipped) candidates against scan budget.
            scanned += 1

            # Dispatch search via a fresh client context.
            try:
                await _dispatch_with_typed_wrap(adapter, instance, dispatch_fn, candidate)
            except EngineDispatchError as exc:
                msg = str(exc)
                logger.warning(
                    "[%s] %ssearch failed for %s: %s",
                    instance.core.name,
                    log_prefix,
                    candidate.item_id,
                    msg,
                )
                await _write_item_log(
                    ref,
                    SearchAction.error.value,
                    search_kind=search_kind,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    message=msg,
                )
                continue

            await record_search_ref(ref, search_kind)
            await _write_item_log(
                ref,
                SearchAction.searched.value,
                search_kind=search_kind,
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                item_label=candidate.label,
            )
            searched += 1
            searches_this_hour += 1
            logger.info(
                "[%s] %ssearched %s %s",
                instance.core.name,
                log_prefix,
                candidate.item_type,
                candidate.item_id,
            )
            await asyncio.sleep(_INTER_SEARCH_DELAY_SECONDS)

        if stop_pass:
            break

        # Only advance to the next page if the current one was fully
        # consumed.  When the batch fills or scan budget is reached
        # mid-page, the remaining items should be re-evaluated next cycle.
        if page_fully_consumed:
            if use_random_deck:
                page = _draw_next_random_page(instance.core.id, search_kind, random_max_page)
            else:
                page += 1

    return searched, page


# Upgrade pass (dedicated, does NOT reuse _run_search_pass)


async def _run_upgrade_pass(
    instance: Instance,
    adapter: AppAdapterProto,
    master_key: bytes,
    *,
    cycle_id: str,
    cycle_trigger: CycleTrigger | str,
) -> int:
    """Execute the upgrade search pass for *instance*.

    Fetches the library via the adapter, applies offset-based rotation,
    cooldown checks, and dispatches searches for upgrade-eligible items.

    Args:
        instance: Fully-populated (decrypted) instance.
        adapter: The :class:`AppAdapterProto` implementation for this
            instance type.
        master_key: Fernet key for persisting offset updates.
        cycle_id: Shared cycle identifier for all log rows.
        cycle_trigger: How this cycle was initiated.

    Returns:
        Count of items searched in this upgrade pass.
    """
    batch_size = min(max(0, instance.upgrade.upgrade_batch_size), _UPGRADE_BATCH_HARD_CAP)
    hourly_cap = min(max(0, instance.upgrade.upgrade_hourly_cap), _UPGRADE_HOURLY_CAP_HARD_CAP)
    cooldown_days = max(instance.upgrade.upgrade_cooldown_days, _UPGRADE_MIN_COOLDOWN_DAYS)
    scan_budget = _clamp(batch_size * 8, _UPGRADE_SCAN_BUDGET_MIN, _UPGRADE_SCAN_BUDGET_MAX)

    if batch_size == 0:
        return 0

    # Fetch upgrade-eligible pool via the adapter
    try:
        pool = await _fetch_pool_with_typed_wrap(adapter, instance)
    except EnginePoolFetchError as exc:
        msg = str(exc)
        logger.warning("[%s] upgrade pool fetch failed: %s", instance.core.name, msg)
        await _write_log(
            instance.core.id,
            None,
            None,
            SearchAction.error.value,
            search_kind="upgrade",
            cycle_id=cycle_id,
            cycle_trigger=cycle_trigger,
            message=f"upgrade pool fetch failed: {msg}",
        )
        return 0

    # Advance series offset for series-based apps (Sonarr/Whisparr v2),
    # but only when the slice produced something. Rotating through an
    # always-empty library (no enabled/monitored series yet) would walk
    # the cursor off into the future for no coverage gain.
    # Unlike upgrade_item_offset, this advances in both chronological and
    # random modes on purpose: the series offset decides which slice of
    # series feeds the upgrade pool, so continuing to rotate it in random
    # mode preserves whole-library coverage while the shuffle happens
    # within each rotated slice.
    if pool and instance.core.type in (InstanceType.sonarr, InstanceType.whisparr_v2):
        new_series_offset = instance.upgrade.upgrade_series_offset + 5
        try:
            await _persist_offset_with_typed_wrap(
                instance.core.id,
                master_key=master_key,
                upgrade_series_offset=new_series_offset,
            )
        except EngineOffsetPersistError:
            logger.warning("[%s] failed to persist upgrade_series_offset", instance.core.name)

    if not pool:
        logger.info("[%s] upgrade pool empty, nothing to upgrade", instance.core.name)
        # Suppress identical info rows on every cycle for an instance
        # that has nothing to upgrade.  One row per 6 hours per
        # instance keeps the logs feed calm while still proving the
        # pass ran periodically.  See `services/cooldown.py::
        # should_log_info` for the LRU + TTL contract.
        from houndarr.services.cooldown import should_log_info

        if await should_log_info((instance.core.id, "upgrade_pool_empty"), 6 * 3600):
            await _write_log(
                instance.core.id,
                None,
                None,
                SearchAction.info.value,
                search_kind="upgrade",
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                message="nothing to upgrade right now",
            )
        return 0

    # Random mode shuffles the pool and bypasses id-sort + offset-rotation.
    # Chronological mode keeps the deterministic rotation so coverage is
    # guaranteed over time.
    if instance.schedule.search_order == SearchOrder.random:
        random.shuffle(pool)  # noqa: S311  # nosec B311
        offset = 0
        rotated = pool
    else:

        def _item_sort_key(item: object) -> int:
            return (
                getattr(item, "movie_id", None)
                or getattr(item, "episode_id", None)
                or getattr(item, "album_id", None)
                or getattr(item, "book_id", None)
                or 0
            )

        pool.sort(key=_item_sort_key)
        offset = instance.upgrade.upgrade_item_offset % len(pool) if pool else 0
        rotated = pool[offset:] + pool[:offset]

    searches_this_hour = await _count_searches_last_hour(instance.core.id, "upgrade")
    searched = 0
    scanned = 0
    seen_item_ids: set[int] = set()
    seen_group_keys: set[tuple[int, int]] = set()
    new_offset = offset

    for item in rotated:
        if searched >= batch_size or scanned >= scan_budget:
            break

        candidate = adapter.adapt_upgrade(item, instance)
        ref = ItemRef(
            instance.core.id,
            candidate.item_id,
            ItemType(candidate.item_type),
        )

        # Dedup
        if candidate.group_key is None:
            if candidate.item_id in seen_item_ids:
                continue
            seen_item_ids.add(candidate.item_id)
        else:
            if candidate.group_key in seen_group_keys:
                continue
            seen_group_keys.add(candidate.group_key)
            if candidate.item_id in seen_item_ids:
                continue
            seen_item_ids.add(candidate.item_id)

        # Hourly cap
        if hourly_cap > 0 and searches_this_hour >= hourly_cap:
            reason = _format_hourly_limit_reason("upgrade", hourly_cap)
            logger.info("[%s] upgrade %s: %s", instance.core.name, candidate.item_id, reason)
            await _write_item_log(
                ref,
                SearchAction.skipped.value,
                search_kind="upgrade",
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                item_label=candidate.label,
                reason=reason,
            )
            break

        # Cooldown
        if await is_on_cooldown_ref(ref, cooldown_days):
            reason = f"on upgrade cooldown ({cooldown_days}d)"
            skip_key = (instance.core.id, candidate.item_id, "upgrade", "upgrade_cd")
            if cycle_trigger == "run_now" or await should_log_skip(skip_key):
                logger.debug("[%s] upgrade %s: %s", instance.core.name, candidate.item_id, reason)
                await _write_item_log(
                    ref,
                    SearchAction.skipped.value,
                    search_kind="upgrade",
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    reason=reason,
                )
            new_offset = (offset + scanned + 1) % len(pool)
            scanned += 1
            continue

        scanned += 1

        # Dispatch search
        try:
            await _dispatch_with_typed_wrap(adapter, instance, adapter.dispatch_search, candidate)
        except EngineDispatchError as exc:
            msg = str(exc)
            logger.warning(
                "[%s] upgrade search failed for %s: %s",
                instance.core.name,
                candidate.item_id,
                msg,
            )
            await _write_item_log(
                ref,
                SearchAction.error.value,
                search_kind="upgrade",
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                item_label=candidate.label,
                message=msg,
            )
            new_offset = (offset + scanned) % len(pool)
            continue

        await record_search_ref(ref, "upgrade")
        await _write_item_log(
            ref,
            SearchAction.searched.value,
            search_kind="upgrade",
            cycle_id=cycle_id,
            cycle_trigger=cycle_trigger,
            item_label=candidate.label,
        )
        searched += 1
        searches_this_hour += 1
        new_offset = (offset + scanned) % len(pool)
        logger.info(
            "[%s] upgrade searched %s %s",
            instance.core.name,
            candidate.item_type,
            candidate.item_id,
        )
        await asyncio.sleep(_INTER_SEARCH_DELAY_SECONDS)

    # Persist new offset.  In random mode the offset concept does not apply
    # (the pool was shuffled, not rotated), so skip the write entirely to
    # keep the column meaningful and avoid per-cycle row churn.
    if instance.schedule.search_order == SearchOrder.chronological:
        try:
            await _persist_offset_with_typed_wrap(
                instance.core.id,
                master_key=master_key,
                upgrade_item_offset=new_offset,
            )
        except EngineOffsetPersistError:
            logger.warning("[%s] failed to persist upgrade_item_offset", instance.core.name)

    return searched


# Public API


async def run_instance_search(
    instance: Instance,
    master_key: bytes,
    *,
    cycle_id: str | None = None,
    cycle_trigger: CycleTrigger | str = CycleTrigger.scheduled,
) -> int:
    """Execute one search cycle for *instance*.

    Steps:
    1. Look up the adapter for the instance type.
    2. Run the missing pass via :func:`_run_search_pass`.
    3. Optionally run the cutoff pass via :func:`_run_search_pass`.
    4. Return the total number of items searched.

    Error surface:
    Typed Houndarr errors (:class:`~houndarr.errors.EngineError` and
    :class:`~houndarr.errors.ClientError` subclasses) and
    :class:`httpx.TransportError` propagate unchanged; the supervisor's
    reconnect loop inspects ``httpx.TransportError`` specifically, and
    its typed catch consumes the two Houndarr bases.  Any other
    exception escaping the internal handlers is wrapped in a fresh
    :class:`EngineError` with the original on ``__cause__`` so callers
    only ever see a Houndarr-specific surface.

    Args:
        instance: Fully-populated (decrypted) instance.
        master_key: Unused here but kept in signature for symmetry with
            supervisor; future callers may need it for re-encryption.

    Returns:
        Count of items searched in this cycle.
    """
    try:
        return await _run_instance_search_impl(
            instance,
            master_key,
            cycle_id=cycle_id,
            cycle_trigger=cycle_trigger,
        )
    except httpx.TransportError:
        raise
    except (EngineError, ClientError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise EngineError(
            f"unhandled error in search cycle for {instance.core.name!r}: {exc}"
        ) from exc


async def _run_instance_search_impl(
    instance: Instance,
    master_key: bytes,
    *,
    cycle_id: str | None = None,
    cycle_trigger: CycleTrigger | str = CycleTrigger.scheduled,
) -> int:
    """Cycle body; wrapped by :func:`run_instance_search` for typed errors.

    Kept as a private implementation so the public entrypoint can own
    the error-surface contract without indenting a 200-line try block.
    Callers outside this module should use :func:`run_instance_search`.
    """
    logger.info(
        "[%s] starting search cycle (batch_size=%d)",
        instance.core.name,
        instance.missing.batch_size,
    )

    adapter = get_adapter(instance.core.type)
    cycle_id_value = cycle_id or str(uuid4())
    searched = 0

    # --- Allowed-time-window gate (scheduled cycles only) ---
    # Manual "Run Now" clicks (cycle_trigger == "run_now") bypass this gate
    # on purpose: the time window is an operator-preference schedule, not a
    # safety gate.  Queue backpressure and hourly caps still apply to manual
    # runs below.
    if cycle_trigger == "scheduled" and instance.schedule.allowed_time_window:
        try:
            ranges = parse_time_window(instance.schedule.allowed_time_window)
        except ValueError:
            # Malformed spec should have been rejected at save time; if it
            # slipped through (e.g. manual DB edit), fail open rather than
            # silently skipping every cycle forever.
            logger.warning(
                "[%s] malformed allowed_time_window %r; ignoring gate",
                instance.core.name,
                instance.schedule.allowed_time_window,
            )
            ranges = []
        if ranges:
            now_local = datetime.now(UTC).astimezone().time()
            if not is_within_window(now_local, ranges):
                reason = "outside allowed time window"
                configured = format_ranges(ranges)
                message = (
                    f"Local time {now_local.strftime('%H:%M')} is outside "
                    f"configured window {configured}"
                )
                logger.info("[%s] skipping cycle: %s (%s)", instance.core.name, reason, message)
                await _write_log(
                    instance.core.id,
                    None,
                    None,
                    SearchAction.info.value,
                    cycle_id=cycle_id_value,
                    cycle_trigger=cycle_trigger,
                    reason=reason,
                    message=message,
                )
                return 0

    # --- Queue backpressure gate ---
    if instance.missing.queue_limit > 0:
        try:
            async with adapter.make_client(instance) as queue_client:
                queue_status = await queue_client.get_queue_status()
            total_queued = queue_status.total_count
            if total_queued >= instance.missing.queue_limit:
                reason = f"queue backpressure ({total_queued}/{instance.missing.queue_limit})"
                logger.info("[%s] skipping cycle: %s", instance.core.name, reason)
                await _write_log(
                    instance.core.id,
                    None,
                    None,
                    SearchAction.info.value,
                    cycle_id=cycle_id_value,
                    cycle_trigger=cycle_trigger,
                    reason=reason,
                    message=(
                        f"Download queue has {total_queued} items,"
                        f" limit is {instance.missing.queue_limit}"
                    ),
                )
                return 0
            logger.debug(
                "[%s] queue check passed (%d/%d)",
                instance.core.name,
                total_queued,
                instance.missing.queue_limit,
            )
        except (ClientError, httpx.InvalidURL):
            # If the queue check fails, log a warning and continue with the
            # search cycle; failing open avoids blocking searches when the
            # queue endpoint is temporarily unavailable or the payload
            # shape drifts.  ``ClientError`` covers HTTP, transport, and
            # validation failures from ``get_queue_status``; raw
            # ``httpx.InvalidURL`` can still surface from client
            # construction in ``adapter.make_client`` before the wrap
            # boundary applies.
            logger.warning(
                "[%s] queue status check failed; proceeding with search",
                instance.core.name,
                exc_info=True,
            )

    # --- Missing pass ---
    missing_target = max(0, instance.missing.batch_size)
    if missing_target > 0:
        client = adapter.make_client(instance)
        async with client:
            missing_searched, next_missing_page = await _run_search_pass(
                instance,
                adapter,
                SearchPassConfig(
                    adapt_fn=adapter.adapt_missing,
                    dispatch_fn=adapter.dispatch_search,
                    fetch_fn=client.get_missing,
                    search_kind="missing",
                    batch_size=instance.missing.batch_size,
                    hourly_cap=instance.missing.hourly_cap,
                    cooldown_days=instance.missing.cooldown_days,
                    page_size=_missing_page_size(missing_target),
                    scan_budget=_missing_scan_budget(missing_target),
                    cycle_id=cycle_id_value,
                    cycle_trigger=cycle_trigger,
                    start_page=instance.schedule.missing_page_offset,
                    total_fn=lambda: client.get_wanted_total("missing"),
                ),
            )
        searched += missing_searched
        # In random mode the "next page" returned by the pass is derived from
        # a random pick, not a rotation offset, so persisting it would write
        # a misleading value to the column and churn the row every cycle.
        # Only advance the offset in chronological mode.
        if instance.schedule.search_order == SearchOrder.chronological:
            try:
                await _persist_offset_with_typed_wrap(
                    instance.core.id,
                    master_key=master_key,
                    missing_page_offset=next_missing_page,
                )
            except EngineOffsetPersistError:
                logger.warning("[%s] failed to persist missing_page_offset", instance.core.name)

    logger.info("[%s] cycle complete: %d searched", instance.core.name, searched)

    # --- Cutoff-unmet pass ---
    if instance.cutoff.cutoff_enabled:
        cutoff_target = max(0, instance.cutoff.cutoff_batch_size)
        if cutoff_target > 0:
            logger.info(
                "[%s] starting cutoff-unmet pass (cutoff_batch_size=%d)",
                instance.core.name,
                instance.cutoff.cutoff_batch_size,
            )
            cutoff_client = adapter.make_client(instance)
            async with cutoff_client:
                cutoff_searched, next_cutoff_page = await _run_search_pass(
                    instance,
                    adapter,
                    SearchPassConfig(
                        adapt_fn=adapter.adapt_cutoff,
                        dispatch_fn=adapter.dispatch_search,
                        fetch_fn=cutoff_client.get_cutoff_unmet,
                        search_kind="cutoff",
                        batch_size=instance.cutoff.cutoff_batch_size,
                        hourly_cap=instance.cutoff.cutoff_hourly_cap,
                        cooldown_days=instance.cutoff.cutoff_cooldown_days,
                        page_size=_cutoff_page_size(cutoff_target),
                        scan_budget=_cutoff_scan_budget(cutoff_target),
                        cycle_id=cycle_id_value,
                        cycle_trigger=cycle_trigger,
                        start_page=instance.schedule.cutoff_page_offset,
                        total_fn=lambda: cutoff_client.get_wanted_total("cutoff"),
                    ),
                )
            logger.info(
                "[%s] cutoff pass complete: %d searched", instance.core.name, cutoff_searched
            )
            # Mirror the missing-pass gate: only advance the offset in
            # chronological mode.  See the missing pass for rationale.
            if instance.schedule.search_order == SearchOrder.chronological:
                try:
                    await _persist_offset_with_typed_wrap(
                        instance.core.id,
                        master_key=master_key,
                        cutoff_page_offset=next_cutoff_page,
                    )
                except EngineOffsetPersistError:
                    logger.warning("[%s] failed to persist cutoff_page_offset", instance.core.name)
            searched += cutoff_searched

    # --- Upgrade pass ---
    if instance.upgrade.upgrade_enabled:
        upgrade_target = min(
            max(0, instance.upgrade.upgrade_batch_size),
            _UPGRADE_BATCH_HARD_CAP,
        )
        if upgrade_target > 0:
            logger.info(
                "[%s] starting upgrade pass (upgrade_batch_size=%d)",
                instance.core.name,
                upgrade_target,
            )
            upgrade_searched = await _run_upgrade_pass(
                instance,
                adapter,
                master_key,
                cycle_id=cycle_id_value,
                cycle_trigger=cycle_trigger,
            )
            logger.info(
                "[%s] upgrade pass complete: %d searched",
                instance.core.name,
                upgrade_searched,
            )
            searched += upgrade_searched

    return searched

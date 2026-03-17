"""Per-instance search loop.

:func:`run_instance_search` is the single entry point called by the supervisor.
It fetches one batch of missing items, applies cooldown and hourly-cap checks,
triggers the *arr search command for each eligible item, and writes a row to
``search_log`` for every item processed.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from houndarr.database import get_db
from houndarr.engine.adapters import AppAdapter, get_adapter
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.cooldown import (
    is_on_cooldown,
    record_search,
)
from houndarr.services.instances import Instance

logger = logging.getLogger(__name__)

SearchKind = Literal["missing", "cutoff"]
CycleTrigger = Literal["scheduled", "run_now", "system"]

_MAX_LIST_PAGES_PER_PASS = 3
_MISSING_PAGE_SIZE_MIN = 10
_MISSING_PAGE_SIZE_MAX = 50
_MISSING_SCAN_BUDGET_MIN = 24
_MISSING_SCAN_BUDGET_MAX = 120
_CUTOFF_PAGE_SIZE_MIN = 5
_CUTOFF_PAGE_SIZE_MAX = 25
_CUTOFF_SCAN_BUDGET_MIN = 12
_CUTOFF_SCAN_BUDGET_MAX = 60


# ---------------------------------------------------------------------------
# search_log helper
# ---------------------------------------------------------------------------


async def _write_log(
    instance_id: int | None,
    item_id: int | None,
    item_type: str | None,
    action: str,
    search_kind: SearchKind | None = None,
    cycle_id: str | None = None,
    cycle_trigger: CycleTrigger | None = None,
    item_label: str | None = None,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    """Insert a single row into ``search_log``."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO search_log
                (
                    instance_id,
                    item_id,
                    item_type,
                    search_kind,
                    cycle_id,
                    cycle_trigger,
                    item_label,
                    action,
                    reason,
                    message
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instance_id,
                item_id,
                item_type,
                search_kind,
                cycle_id,
                cycle_trigger,
                item_label,
                action,
                reason,
                message,
            ),
        )
        await db.commit()


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


async def _count_searches_last_hour(instance_id: int, search_kind: SearchKind) -> int:
    """Count successful searches in the last hour for one pass kind."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM search_log
            WHERE instance_id = ?
              AND action = 'searched'
              AND search_kind = ?
              AND timestamp > ?
            """,
            (instance_id, search_kind, cutoff_iso),
        ) as cur:
            row = await cur.fetchone()

    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Unified search pass
# ---------------------------------------------------------------------------


async def _run_search_pass(  # noqa: C901
    instance: Instance,
    adapter: AppAdapter,
    *,
    adapt_fn: Callable[..., SearchCandidate],
    dispatch_fn: Callable[..., Awaitable[None]],
    fetch_fn: Callable[..., Awaitable[list[Any]]],
    search_kind: SearchKind,
    batch_size: int,
    hourly_cap: int,
    cooldown_days: int,
    page_size: int,
    scan_budget: int,
    cycle_id: str,
    cycle_trigger: CycleTrigger,
) -> int:
    """Execute a single search pass (missing or cutoff) using the adapter.

    This is the unified pipeline that replaces the previously duplicated
    missing-pass inline code and the bifurcated ``_run_cutoff_pass()``
    function.  It pages through items, converts each to a
    :class:`SearchCandidate` via *adapt_fn*, applies eligibility checks
    (unreleased delay, hourly cap, cooldown), and dispatches searches via
    *dispatch_fn*.

    Args:
        instance: Fully-populated (decrypted) instance.
        adapter: The :class:`AppAdapter` for this instance type.
        adapt_fn: Converts a raw API item to a :class:`SearchCandidate`.
        dispatch_fn: Sends the search command via the appropriate client.
        fetch_fn: Bound method to fetch a page of items
            (e.g. ``client.get_missing`` or ``client.get_cutoff_unmet``).
        search_kind: ``"missing"`` or ``"cutoff"``.
        batch_size: Maximum items to search in this pass.
        hourly_cap: Hourly search limit for this pass kind (0 = unlimited).
        cooldown_days: Cooldown window for this pass kind.
        page_size: Number of items to request per page.
        scan_budget: Maximum candidates to evaluate before stopping.
        cycle_id: Shared cycle identifier for all log rows.
        cycle_trigger: How this cycle was initiated.

    Returns:
        Count of items searched in this pass.
    """
    target = max(0, batch_size)
    if target == 0:
        return 0

    is_cutoff = search_kind == "cutoff"
    log_prefix = "cutoff " if is_cutoff else ""

    searches_this_hour = await _count_searches_last_hour(instance.id, search_kind)
    seen_item_ids: set[int] = set()
    seen_group_keys: set[tuple[int, int]] = set()
    searched = 0
    scanned = 0
    page = 1

    for _ in range(_MAX_LIST_PAGES_PER_PASS):
        if searched >= target or scanned >= scan_budget:
            break

        items = await fetch_fn(page=page, page_size=page_size)
        logger.debug(
            "[%s] fetched %d %sitem(s) from page %d",
            instance.name,
            len(items),
            "cutoff-unmet " if is_cutoff else "missing ",
            page,
        )
        if not items:
            break

        stop_pass = False
        for item in items:
            if searched >= target or scanned >= scan_budget:
                break

            candidate = adapt_fn(item, instance)

            # Group-key dedup (season-context mode).
            if candidate.group_key is not None:
                if candidate.group_key in seen_group_keys:
                    continue
                seen_group_keys.add(candidate.group_key)

            # Item-id dedup across pages.
            if candidate.item_id in seen_item_ids:
                continue
            seen_item_ids.add(candidate.item_id)
            scanned += 1

            # Unreleased delay.
            if candidate.unreleased_reason is not None:
                await _write_log(
                    instance.id,
                    candidate.item_id,
                    candidate.item_type,
                    "skipped",
                    search_kind=search_kind,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    reason=candidate.unreleased_reason,
                )
                continue

            # Hourly cap.
            if hourly_cap > 0 and searches_this_hour >= hourly_cap:
                reason = (
                    f"cutoff hourly cap reached ({hourly_cap})"
                    if is_cutoff
                    else f"hourly cap reached ({hourly_cap})"
                )
                logger.info("[%s] %s%s — %s", instance.name, log_prefix, candidate.item_id, reason)
                await _write_log(
                    instance.id,
                    candidate.item_id,
                    candidate.item_type,
                    "skipped",
                    search_kind=search_kind,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    reason=reason,
                )
                stop_pass = True
                break

            # Cooldown.
            if await is_on_cooldown(
                instance.id, candidate.item_id, candidate.item_type, cooldown_days
            ):
                reason = (
                    f"on cutoff cooldown ({cooldown_days}d)"
                    if is_cutoff
                    else f"on cooldown ({cooldown_days}d)"
                )
                logger.debug("[%s] %s%s — %s", instance.name, log_prefix, candidate.item_id, reason)
                await _write_log(
                    instance.id,
                    candidate.item_id,
                    candidate.item_type,
                    "skipped",
                    search_kind=search_kind,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    reason=reason,
                )
                continue

            # Dispatch search via a fresh client context.
            try:
                async with adapter.make_client(instance) as dispatch_client:
                    await dispatch_fn(dispatch_client, candidate)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                logger.warning(
                    "[%s] %ssearch failed for %s: %s",
                    instance.name,
                    log_prefix,
                    candidate.item_id,
                    msg,
                )
                await _write_log(
                    instance.id,
                    candidate.item_id,
                    candidate.item_type,
                    "error",
                    search_kind=search_kind,
                    cycle_id=cycle_id,
                    cycle_trigger=cycle_trigger,
                    item_label=candidate.label,
                    message=msg,
                )
                continue

            await record_search(instance.id, candidate.item_id, candidate.item_type)
            await _write_log(
                instance.id,
                candidate.item_id,
                candidate.item_type,
                "searched",
                search_kind=search_kind,
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                item_label=candidate.label,
            )
            searched += 1
            searches_this_hour += 1
            logger.info(
                "[%s] %ssearched %s %s",
                instance.name,
                log_prefix,
                candidate.item_type,
                candidate.item_id,
            )

        if stop_pass:
            break

        page += 1

    return searched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_instance_search(
    instance: Instance,
    master_key: bytes,
    *,
    cycle_id: str | None = None,
    cycle_trigger: CycleTrigger = "scheduled",
) -> int:
    """Execute one search cycle for *instance*.

    Steps:
    1. Look up the adapter for the instance type.
    2. Run the missing pass via :func:`_run_search_pass`.
    3. Optionally run the cutoff pass via :func:`_run_search_pass`.
    4. Return the total number of items searched.

    Args:
        instance: Fully-populated (decrypted) instance.
        master_key: Unused here but kept in signature for symmetry with
            supervisor; future callers may need it for re-encryption.

    Returns:
        Count of items searched in this cycle.
    """
    logger.info(
        "[%s] starting search cycle (batch_size=%d)",
        instance.name,
        instance.batch_size,
    )

    adapter = get_adapter(instance.type)
    client = adapter.make_client(instance)
    cycle_id_value = cycle_id or str(uuid4())
    searched = 0

    # --- Missing pass ---
    missing_target = max(0, instance.batch_size)
    if missing_target > 0:
        async with client:
            searched += await _run_search_pass(
                instance,
                adapter,
                adapt_fn=adapter.adapt_missing,
                dispatch_fn=adapter.dispatch_search,
                fetch_fn=client.get_missing,
                search_kind="missing",
                batch_size=instance.batch_size,
                hourly_cap=instance.hourly_cap,
                cooldown_days=instance.cooldown_days,
                page_size=_missing_page_size(missing_target),
                scan_budget=_missing_scan_budget(missing_target),
                cycle_id=cycle_id_value,
                cycle_trigger=cycle_trigger,
            )

    logger.info("[%s] cycle complete — %d searched", instance.name, searched)

    # --- Cutoff-unmet pass ---
    if instance.cutoff_enabled:
        cutoff_target = max(0, instance.cutoff_batch_size)
        if cutoff_target > 0:
            logger.info(
                "[%s] starting cutoff-unmet pass (cutoff_batch_size=%d)",
                instance.name,
                instance.cutoff_batch_size,
            )
            cutoff_client = adapter.make_client(instance)
            async with cutoff_client:
                cutoff_searched = await _run_search_pass(
                    instance,
                    adapter,
                    adapt_fn=adapter.adapt_cutoff,
                    dispatch_fn=adapter.dispatch_search,
                    fetch_fn=cutoff_client.get_cutoff_unmet,
                    search_kind="cutoff",
                    batch_size=instance.cutoff_batch_size,
                    hourly_cap=instance.cutoff_hourly_cap,
                    cooldown_days=instance.cutoff_cooldown_days,
                    page_size=_cutoff_page_size(cutoff_target),
                    scan_budget=_cutoff_scan_budget(cutoff_target),
                    cycle_id=cycle_id_value,
                    cycle_trigger=cycle_trigger,
                )
            logger.info("[%s] cutoff pass complete — %d searched", instance.name, cutoff_searched)
            searched += cutoff_searched

    return searched

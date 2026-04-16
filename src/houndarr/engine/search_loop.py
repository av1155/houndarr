"""Per-instance search loop.

:func:`run_instance_search` is the single entry point called by the supervisor.
It fetches one batch of missing items, applies cooldown and hourly-cap checks,
triggers the *arr search command for each eligible item, and writes a row to
``search_log`` for every item processed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import httpx

from houndarr.database import get_db
from houndarr.engine.adapters import AppAdapter, get_adapter
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.cooldown import (
    is_on_cooldown,
    record_search,
)
from houndarr.services.instances import Instance, InstanceType, update_instance
from houndarr.services.time_window import (
    format_ranges,
    is_within_window,
    parse_time_window,
)

logger = logging.getLogger(__name__)

SearchKind = Literal["missing", "cutoff", "upgrade"]
CycleTrigger = Literal["scheduled", "run_now", "system"]

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


async def _latest_missing_reason(
    instance_id: int,
    item_id: int,
    item_type: str,
) -> str | None:
    """Return the latest logged missing-pass reason for one item, if any."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT reason
            FROM search_log
            WHERE instance_id = ?
              AND item_id = ?
              AND item_type = ?
              AND search_kind = 'missing'
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (instance_id, item_id, item_type),
        ) as cur:
            row = await cur.fetchone()

    return str(row[0]) if row and row[0] is not None else None


def _is_release_timing_reason(reason: str | None) -> bool:
    """Return ``True`` when *reason* indicates a release-timing block."""
    return reason == "not yet released" or (
        reason is not None and reason.startswith("post-release grace")
    )


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
    start_page: int = 1,
) -> tuple[int, int]:
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
        start_page: 1-based page number to begin fetching from (for
            offset rotation across cycles).

    Returns:
        Tuple of (items_searched, next_start_page).
    """
    target = max(0, batch_size)
    if target == 0:
        return 0, start_page

    is_cutoff = search_kind == "cutoff"
    log_prefix = "cutoff " if is_cutoff else ""

    searches_this_hour = await _count_searches_last_hour(instance.id, search_kind)
    seen_item_ids: set[int] = set()
    seen_group_keys: set[tuple[int, int]] = set()
    searched = 0
    scanned = 0
    page = max(1, start_page)
    wrapped = False

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
            # Paged past available data; wrap to page 1 once per pass so
            # items at the start of the list (which may have come off
            # cooldown) still get evaluated.
            if start_page > 1 and not wrapped:
                page = 1
                wrapped = True
                continue
            break

        stop_pass = False
        page_fully_consumed = True
        for item in items:
            if searched >= target or scanned >= scan_budget:
                page_fully_consumed = False
                break

            candidate = adapt_fn(item, instance)

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
                reason = (
                    f"cutoff hourly cap reached ({hourly_cap})"
                    if is_cutoff
                    else f"hourly cap reached ({hourly_cap})"
                )
                logger.info("[%s] %s%s: %s", instance.name, log_prefix, candidate.item_id, reason)
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
                if search_kind == "missing":
                    latest_reason = await _latest_missing_reason(
                        instance.id, candidate.item_id, candidate.item_type
                    )
                    if _is_release_timing_reason(latest_reason):
                        logger.info(
                            "[%s] allowing missing retry for %s after release-timing block",
                            instance.name,
                            candidate.item_id,
                        )
                        scanned += 1
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
                        await asyncio.sleep(_INTER_SEARCH_DELAY_SECONDS)
                        continue

                reason = (
                    f"on cutoff cooldown ({cooldown_days}d)"
                    if is_cutoff
                    else f"on cooldown ({cooldown_days}d)"
                )
                logger.debug("[%s] %s%s: %s", instance.name, log_prefix, candidate.item_id, reason)
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

            # Count only eligible (non-skipped) candidates against scan budget.
            scanned += 1

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
            await asyncio.sleep(_INTER_SEARCH_DELAY_SECONDS)

        if stop_pass:
            break

        # Only advance to the next page if the current one was fully
        # consumed.  When the batch fills or scan budget is reached
        # mid-page, the remaining items should be re-evaluated next cycle.
        if page_fully_consumed:
            page += 1

    return searched, page


# ---------------------------------------------------------------------------
# Upgrade pass (dedicated, does NOT reuse _run_search_pass)
# ---------------------------------------------------------------------------


async def _run_upgrade_pass(
    instance: Instance,
    adapter: AppAdapter,
    master_key: bytes,
    *,
    cycle_id: str,
    cycle_trigger: CycleTrigger,
) -> int:
    """Execute the upgrade search pass for *instance*.

    Fetches the library via the adapter, applies offset-based rotation,
    cooldown checks, and dispatches searches for upgrade-eligible items.

    Args:
        instance: Fully-populated (decrypted) instance.
        adapter: The :class:`AppAdapter` for this instance type.
        master_key: Fernet key for persisting offset updates.
        cycle_id: Shared cycle identifier for all log rows.
        cycle_trigger: How this cycle was initiated.

    Returns:
        Count of items searched in this upgrade pass.
    """
    batch_size = min(max(0, instance.upgrade_batch_size), _UPGRADE_BATCH_HARD_CAP)
    hourly_cap = min(max(0, instance.upgrade_hourly_cap), _UPGRADE_HOURLY_CAP_HARD_CAP)
    cooldown_days = max(instance.upgrade_cooldown_days, _UPGRADE_MIN_COOLDOWN_DAYS)
    scan_budget = _clamp(batch_size * 8, _UPGRADE_SCAN_BUDGET_MIN, _UPGRADE_SCAN_BUDGET_MAX)

    if batch_size == 0:
        return 0

    # Fetch upgrade-eligible pool via the adapter
    try:
        async with adapter.make_client(instance) as client:
            pool = await adapter.fetch_upgrade_pool(client, instance)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        logger.warning("[%s] upgrade pool fetch failed: %s", instance.name, msg)
        await _write_log(
            instance.id,
            None,
            None,
            "error",
            search_kind="upgrade",
            cycle_id=cycle_id,
            cycle_trigger=cycle_trigger,
            message=f"upgrade pool fetch failed: {msg}",
        )
        return 0

    # Advance series offset for series-based apps (Sonarr/Whisparr)
    if instance.type in (InstanceType.sonarr, InstanceType.whisparr_v2):
        new_series_offset = instance.upgrade_series_offset + 5
        try:
            await update_instance(
                instance.id,
                master_key=master_key,
                upgrade_series_offset=new_series_offset,
            )
        except Exception:  # noqa: BLE001
            logger.warning("[%s] failed to persist upgrade_series_offset", instance.name)

    if not pool:
        logger.info("[%s] upgrade pool empty, nothing to upgrade", instance.name)
        await _write_log(
            instance.id,
            None,
            None,
            "info",
            search_kind="upgrade",
            cycle_id=cycle_id,
            cycle_trigger=cycle_trigger,
            message="upgrade pool empty",
        )
        return 0

    # Sort by item ID for stable ordering
    def _item_sort_key(item: object) -> int:
        return (
            getattr(item, "movie_id", None)
            or getattr(item, "episode_id", None)
            or getattr(item, "album_id", None)
            or getattr(item, "book_id", None)
            or 0
        )

    pool.sort(key=_item_sort_key)

    # Apply offset-based rotation
    offset = instance.upgrade_item_offset % len(pool) if pool else 0
    rotated = pool[offset:] + pool[:offset]

    searches_this_hour = await _count_searches_last_hour(instance.id, "upgrade")
    searched = 0
    scanned = 0
    seen_item_ids: set[int] = set()
    seen_group_keys: set[tuple[int, int]] = set()
    new_offset = offset

    for item in rotated:
        if searched >= batch_size or scanned >= scan_budget:
            break

        candidate = adapter.adapt_upgrade(item, instance)

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
            reason = f"upgrade hourly cap reached ({hourly_cap})"
            logger.info("[%s] upgrade %s: %s", instance.name, candidate.item_id, reason)
            await _write_log(
                instance.id,
                candidate.item_id,
                candidate.item_type,
                "skipped",
                search_kind="upgrade",
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                item_label=candidate.label,
                reason=reason,
            )
            break

        # Cooldown
        if await is_on_cooldown(instance.id, candidate.item_id, candidate.item_type, cooldown_days):
            reason = f"on upgrade cooldown ({cooldown_days}d)"
            logger.debug("[%s] upgrade %s: %s", instance.name, candidate.item_id, reason)
            await _write_log(
                instance.id,
                candidate.item_id,
                candidate.item_type,
                "skipped",
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
            async with adapter.make_client(instance) as dispatch_client:
                await adapter.dispatch_search(dispatch_client, candidate)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.warning(
                "[%s] upgrade search failed for %s: %s",
                instance.name,
                candidate.item_id,
                msg,
            )
            await _write_log(
                instance.id,
                candidate.item_id,
                candidate.item_type,
                "error",
                search_kind="upgrade",
                cycle_id=cycle_id,
                cycle_trigger=cycle_trigger,
                item_label=candidate.label,
                message=msg,
            )
            new_offset = (offset + scanned) % len(pool)
            continue

        await record_search(instance.id, candidate.item_id, candidate.item_type)
        await _write_log(
            instance.id,
            candidate.item_id,
            candidate.item_type,
            "searched",
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
            instance.name,
            candidate.item_type,
            candidate.item_id,
        )
        await asyncio.sleep(_INTER_SEARCH_DELAY_SECONDS)

    # Persist new offset
    try:
        await update_instance(
            instance.id,
            master_key=master_key,
            upgrade_item_offset=new_offset,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[%s] failed to persist upgrade_item_offset", instance.name)

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
    cycle_id_value = cycle_id or str(uuid4())
    searched = 0

    # --- Allowed-time-window gate (scheduled cycles only) ---
    # Manual "Run Now" clicks (cycle_trigger == "run_now") bypass this gate
    # on purpose: the time window is an operator-preference schedule, not a
    # safety gate.  Queue backpressure and hourly caps still apply to manual
    # runs below.
    if cycle_trigger == "scheduled" and instance.allowed_time_window:
        try:
            ranges = parse_time_window(instance.allowed_time_window)
        except ValueError:
            # Malformed spec should have been rejected at save time; if it
            # slipped through (e.g. manual DB edit), fail open rather than
            # silently skipping every cycle forever.
            logger.warning(
                "[%s] malformed allowed_time_window %r; ignoring gate",
                instance.name,
                instance.allowed_time_window,
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
                logger.info("[%s] skipping cycle: %s (%s)", instance.name, reason, message)
                await _write_log(
                    instance.id,
                    None,
                    None,
                    "info",
                    cycle_id=cycle_id_value,
                    cycle_trigger=cycle_trigger,
                    reason=reason,
                    message=message,
                )
                return 0

    # --- Queue backpressure gate ---
    if instance.queue_limit > 0:
        try:
            async with adapter.make_client(instance) as queue_client:
                queue_status = await queue_client.get_queue_status()
            total_queued = int(queue_status.get("totalCount", 0))
            if total_queued >= instance.queue_limit:
                reason = f"queue backpressure ({total_queued}/{instance.queue_limit})"
                logger.info("[%s] skipping cycle: %s", instance.name, reason)
                await _write_log(
                    instance.id,
                    None,
                    None,
                    "info",
                    cycle_id=cycle_id_value,
                    cycle_trigger=cycle_trigger,
                    reason=reason,
                    message=(
                        f"Download queue has {total_queued} items, limit is {instance.queue_limit}"
                    ),
                )
                return 0
            logger.debug(
                "[%s] queue check passed (%d/%d)",
                instance.name,
                total_queued,
                instance.queue_limit,
            )
        except (httpx.HTTPError, httpx.InvalidURL, KeyError, ValueError):
            # If the queue check fails, log a warning and continue with the
            # search cycle; failing open avoids blocking searches when the
            # queue endpoint is temporarily unavailable.
            logger.warning(
                "[%s] queue status check failed; proceeding with search",
                instance.name,
                exc_info=True,
            )

    # --- Missing pass ---
    missing_target = max(0, instance.batch_size)
    if missing_target > 0:
        client = adapter.make_client(instance)
        async with client:
            missing_searched, next_missing_page = await _run_search_pass(
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
                start_page=instance.missing_page_offset,
            )
        searched += missing_searched
        try:
            await update_instance(
                instance.id,
                master_key=master_key,
                missing_page_offset=next_missing_page,
            )
        except Exception:  # noqa: BLE001
            logger.warning("[%s] failed to persist missing_page_offset", instance.name)

    logger.info("[%s] cycle complete: %d searched", instance.name, searched)

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
                cutoff_searched, next_cutoff_page = await _run_search_pass(
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
                    start_page=instance.cutoff_page_offset,
                )
            logger.info("[%s] cutoff pass complete: %d searched", instance.name, cutoff_searched)
            try:
                await update_instance(
                    instance.id,
                    master_key=master_key,
                    cutoff_page_offset=next_cutoff_page,
                )
            except Exception:  # noqa: BLE001
                logger.warning("[%s] failed to persist cutoff_page_offset", instance.name)
            searched += cutoff_searched

    # --- Upgrade pass ---
    if instance.upgrade_enabled:
        upgrade_target = min(
            max(0, instance.upgrade_batch_size),
            _UPGRADE_BATCH_HARD_CAP,
        )
        if upgrade_target > 0:
            logger.info(
                "[%s] starting upgrade pass (upgrade_batch_size=%d)",
                instance.name,
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
                instance.name,
                upgrade_searched,
            )
            searched += upgrade_searched

    return searched

"""Per-instance search loop.

:func:`run_instance_search` is the single entry point called by the supervisor.
It fetches one batch of missing items, applies cooldown and hourly-cap checks,
triggers the *arr search command for each eligible item, and writes a row to
``search_log`` for every item processed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from houndarr.clients.radarr import MissingMovie, RadarrClient
from houndarr.clients.sonarr import MissingEpisode, SonarrClient
from houndarr.database import get_db
from houndarr.services.cooldown import (
    is_on_cooldown,
    record_search,
)
from houndarr.services.instances import Instance, InstanceType

logger = logging.getLogger(__name__)

SearchKind = Literal["missing", "cutoff"]
CycleTrigger = Literal["scheduled", "run_now", "system"]
ItemType = Literal["episode", "movie"]

_MAX_LIST_PAGES_PER_PASS = 3
_MISSING_PAGE_SIZE_MIN = 10
_MISSING_PAGE_SIZE_MAX = 50
_MISSING_SCAN_BUDGET_MIN = 24
_MISSING_SCAN_BUDGET_MAX = 120
_CUTOFF_PAGE_SIZE_MIN = 5
_CUTOFF_PAGE_SIZE_MAX = 25
_CUTOFF_SCAN_BUDGET_MIN = 12
_CUTOFF_SCAN_BUDGET_MAX = 60
_RADARR_UNRELEASED_STATUSES = {"tba", "announced"}


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


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 value into a timezone-aware UTC datetime."""
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_within_unreleased_delay(release_at: str | None, unreleased_delay_hrs: int) -> bool:
    """Return True when an item is still inside the configured unreleased delay."""
    if unreleased_delay_hrs <= 0:
        return False

    release_dt = _parse_iso_utc(release_at)
    if release_dt is None:
        return False

    return datetime.now(UTC) < (release_dt + timedelta(hours=unreleased_delay_hrs))


def _radarr_release_anchor(movie: MissingMovie) -> str | None:
    """Return preferred Radarr release anchor in fallback order."""
    return movie.digital_release or movie.physical_release or movie.release_date or movie.in_cinemas


def _radarr_unreleased_reason(movie: MissingMovie, unreleased_delay_hrs: int) -> str | None:
    """Return skip reason when a Radarr movie should be treated as unreleased."""
    release_anchor = _radarr_release_anchor(movie)
    if _is_within_unreleased_delay(release_anchor, unreleased_delay_hrs):
        return f"unreleased delay ({unreleased_delay_hrs}h)"

    if movie.is_available is False:
        return "radarr reports not available"

    status = (movie.status or "").lower()
    if status in _RADARR_UNRELEASED_STATUSES and movie.is_available is not True:
        return "radarr status indicates unreleased"

    if (
        movie.year > datetime.now(UTC).year
        and movie.is_available is not True
        and status != "released"
    ):
        return "future title not yet available"

    return None


def _episode_label(item: MissingEpisode) -> str:
    """Build a human-readable log label for Sonarr episodes."""
    code = f"S{item.season:02d}E{item.episode:02d}"
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - {code} - {item.episode_title}"
    return f"{series} - {code}"


def _movie_label(item: MissingMovie) -> str:
    """Build a human-readable log label for Radarr movies."""
    title = item.title or "Unknown Movie"
    if item.year > 0:
        return f"{title} ({item.year})"
    return title


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
    1. Build the appropriate client (Sonarr or Radarr).
    2. Fetch one page of missing items (size = ``instance.batch_size``).
    3. For each item:
       - If the hourly cap is reached → log *skipped* and stop.
       - If the item is on cooldown → log *skipped* and continue.
       - Otherwise → trigger search, record cooldown, log *searched*.
    4. Return the number of items actually searched.

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

    searched = 0
    cycle_id_value = cycle_id or str(uuid4())
    missing_target = max(0, instance.batch_size)
    missing_page_size = _missing_page_size(missing_target)
    missing_scan_budget = _missing_scan_budget(missing_target)

    if instance.type == InstanceType.sonarr:
        client: SonarrClient | RadarrClient = SonarrClient(
            url=instance.url, api_key=instance.api_key
        )
        item_type: ItemType = "episode"
    else:
        client = RadarrClient(url=instance.url, api_key=instance.api_key)
        item_type = "movie"

    if missing_target > 0:
        searches_this_hour = await _count_searches_last_hour(instance.id, "missing")
        seen_item_ids: set[int] = set()
        scanned = 0
        page = 1

        async with client:
            for _ in range(_MAX_LIST_PAGES_PER_PASS):
                if searched >= missing_target or scanned >= missing_scan_budget:
                    break

                items = await client.get_missing(page=page, page_size=missing_page_size)
                logger.debug(
                    "[%s] fetched %d missing %s(s) from page %d",
                    instance.name,
                    len(items),
                    item_type,
                    page,
                )
                if not items:
                    break

                stop_pass = False
                for item in items:
                    if searched >= missing_target or scanned >= missing_scan_budget:
                        break

                    if isinstance(item, MissingEpisode):
                        item_id = item.episode_id
                        item_label = _episode_label(item)
                        unreleased_reason = (
                            f"unreleased delay ({instance.unreleased_delay_hrs}h)"
                            if _is_within_unreleased_delay(
                                item.air_date_utc, instance.unreleased_delay_hrs
                            )
                            else None
                        )
                    else:
                        item_id = item.movie_id
                        item_label = _movie_label(item)
                        unreleased_reason = _radarr_unreleased_reason(
                            item, instance.unreleased_delay_hrs
                        )

                    if item_id in seen_item_ids:
                        continue
                    seen_item_ids.add(item_id)
                    scanned += 1

                    if unreleased_reason is not None:
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="missing",
                            cycle_id=cycle_id_value,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=unreleased_reason,
                        )
                        continue

                    if instance.hourly_cap > 0 and searches_this_hour >= instance.hourly_cap:
                        reason = f"hourly cap reached ({instance.hourly_cap})"
                        logger.info("[%s] %s — %s", instance.name, item_id, reason)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="missing",
                            cycle_id=cycle_id_value,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        stop_pass = True
                        break

                    if await is_on_cooldown(
                        instance.id, item_id, item_type, instance.cooldown_days
                    ):
                        reason = f"on cooldown ({instance.cooldown_days}d)"
                        logger.debug("[%s] %s — %s", instance.name, item_id, reason)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="missing",
                            cycle_id=cycle_id_value,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        continue

                    try:
                        async with client.__class__(
                            url=instance.url, api_key=instance.api_key
                        ) as c:
                            await c.search(item_id)
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc)
                        logger.warning("[%s] search failed for %s: %s", instance.name, item_id, msg)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "error",
                            search_kind="missing",
                            cycle_id=cycle_id_value,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            message=msg,
                        )
                        continue

                    await record_search(instance.id, item_id, item_type)
                    await _write_log(
                        instance.id,
                        item_id,
                        item_type,
                        "searched",
                        search_kind="missing",
                        cycle_id=cycle_id_value,
                        cycle_trigger=cycle_trigger,
                        item_label=item_label,
                    )
                    searched += 1
                    searches_this_hour += 1
                    logger.info("[%s] searched %s %s", instance.name, item_type, item_id)

                if stop_pass:
                    break

                page += 1

    logger.info("[%s] cycle complete — %d searched", instance.name, searched)

    # -----------------------------------------------------------------------
    # Cutoff-unmet pass (only when enabled)
    # -----------------------------------------------------------------------
    if instance.cutoff_enabled:
        searched += await _run_cutoff_pass(
            instance,
            cycle_id=cycle_id_value,
            cycle_trigger=cycle_trigger,
        )

    return searched


async def _run_cutoff_pass(
    instance: Instance,
    *,
    cycle_id: str,
    cycle_trigger: CycleTrigger,
) -> int:
    """Execute the cutoff-unmet search pass for *instance*.

    Fetches one page of cutoff-unmet items and searches each eligible one,
    applying the same hourly-cap and cooldown logic used for missing items.

    Args:
        instance: Fully-populated (decrypted) instance.

    Returns:
        Count of items searched in this pass.
    """
    item_type: ItemType = "episode" if instance.type == InstanceType.sonarr else "movie"
    logger.info(
        "[%s] starting cutoff-unmet pass (cutoff_batch_size=%d)",
        instance.name,
        instance.cutoff_batch_size,
    )

    searched = 0
    cutoff_target = max(0, instance.cutoff_batch_size)
    cutoff_page_size = _cutoff_page_size(cutoff_target)
    cutoff_scan_budget = _cutoff_scan_budget(cutoff_target)

    if cutoff_target == 0:
        logger.info("[%s] cutoff pass complete — 0 searched", instance.name)
        return 0

    searches_this_hour = await _count_searches_last_hour(instance.id, "cutoff")
    seen_item_ids: set[int] = set()
    scanned = 0
    page = 1

    if instance.type == InstanceType.sonarr:
        sonarr = SonarrClient(url=instance.url, api_key=instance.api_key)
        async with sonarr:
            for _ in range(_MAX_LIST_PAGES_PER_PASS):
                if searched >= cutoff_target or scanned >= cutoff_scan_budget:
                    break

                sonarr_items = await sonarr.get_cutoff_unmet(page=page, page_size=cutoff_page_size)
                logger.debug(
                    "[%s] fetched %d cutoff-unmet %s(s) from page %d",
                    instance.name,
                    len(sonarr_items),
                    item_type,
                    page,
                )
                if not sonarr_items:
                    break

                stop_pass = False
                for episode_item in sonarr_items:
                    if searched >= cutoff_target or scanned >= cutoff_scan_budget:
                        break

                    item_id = episode_item.episode_id
                    if item_id in seen_item_ids:
                        continue
                    seen_item_ids.add(item_id)
                    scanned += 1

                    item_label = _episode_label(episode_item)
                    if _is_within_unreleased_delay(
                        episode_item.air_date_utc, instance.unreleased_delay_hrs
                    ):
                        reason = f"unreleased delay ({instance.unreleased_delay_hrs}h)"
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        continue

                    if (
                        instance.cutoff_hourly_cap > 0
                        and searches_this_hour >= instance.cutoff_hourly_cap
                    ):
                        reason = f"cutoff hourly cap reached ({instance.cutoff_hourly_cap})"
                        logger.info("[%s] cutoff %s — %s", instance.name, item_id, reason)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        stop_pass = True
                        break

                    if await is_on_cooldown(
                        instance.id,
                        item_id,
                        item_type,
                        instance.cutoff_cooldown_days,
                    ):
                        reason = f"on cutoff cooldown ({instance.cutoff_cooldown_days}d)"
                        logger.debug("[%s] cutoff %s — %s", instance.name, item_id, reason)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        continue

                    try:
                        async with SonarrClient(url=instance.url, api_key=instance.api_key) as c:
                            await c.search(item_id)
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc)
                        logger.warning(
                            "[%s] cutoff search failed for %s: %s",
                            instance.name,
                            item_id,
                            msg,
                        )
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "error",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            message=msg,
                        )
                        continue

                    await record_search(instance.id, item_id, item_type)
                    await _write_log(
                        instance.id,
                        item_id,
                        item_type,
                        "searched",
                        search_kind="cutoff",
                        cycle_id=cycle_id,
                        cycle_trigger=cycle_trigger,
                        item_label=item_label,
                    )
                    searched += 1
                    searches_this_hour += 1
                    logger.info("[%s] cutoff searched %s %s", instance.name, item_type, item_id)

                if stop_pass:
                    break

                page += 1
    else:
        radarr = RadarrClient(url=instance.url, api_key=instance.api_key)
        async with radarr:
            for _ in range(_MAX_LIST_PAGES_PER_PASS):
                if searched >= cutoff_target or scanned >= cutoff_scan_budget:
                    break

                radarr_items = await radarr.get_cutoff_unmet(page=page, page_size=cutoff_page_size)
                logger.debug(
                    "[%s] fetched %d cutoff-unmet %s(s) from page %d",
                    instance.name,
                    len(radarr_items),
                    item_type,
                    page,
                )
                if not radarr_items:
                    break

                stop_pass = False
                for movie_item in radarr_items:
                    if searched >= cutoff_target or scanned >= cutoff_scan_budget:
                        break

                    item_id = movie_item.movie_id
                    if item_id in seen_item_ids:
                        continue
                    seen_item_ids.add(item_id)
                    scanned += 1

                    item_label = _movie_label(movie_item)
                    unreleased_reason = _radarr_unreleased_reason(
                        movie_item, instance.unreleased_delay_hrs
                    )
                    if unreleased_reason is not None:
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=unreleased_reason,
                        )
                        continue

                    if (
                        instance.cutoff_hourly_cap > 0
                        and searches_this_hour >= instance.cutoff_hourly_cap
                    ):
                        reason = f"cutoff hourly cap reached ({instance.cutoff_hourly_cap})"
                        logger.info("[%s] cutoff %s — %s", instance.name, item_id, reason)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        stop_pass = True
                        break

                    if await is_on_cooldown(
                        instance.id,
                        item_id,
                        item_type,
                        instance.cutoff_cooldown_days,
                    ):
                        reason = f"on cutoff cooldown ({instance.cutoff_cooldown_days}d)"
                        logger.debug("[%s] cutoff %s — %s", instance.name, item_id, reason)
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "skipped",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            reason=reason,
                        )
                        continue

                    try:
                        async with RadarrClient(url=instance.url, api_key=instance.api_key) as c:
                            await c.search(item_id)
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc)
                        logger.warning(
                            "[%s] cutoff search failed for %s: %s",
                            instance.name,
                            item_id,
                            msg,
                        )
                        await _write_log(
                            instance.id,
                            item_id,
                            item_type,
                            "error",
                            search_kind="cutoff",
                            cycle_id=cycle_id,
                            cycle_trigger=cycle_trigger,
                            item_label=item_label,
                            message=msg,
                        )
                        continue

                    await record_search(instance.id, item_id, item_type)
                    await _write_log(
                        instance.id,
                        item_id,
                        item_type,
                        "searched",
                        search_kind="cutoff",
                        cycle_id=cycle_id,
                        cycle_trigger=cycle_trigger,
                        item_label=item_label,
                    )
                    searched += 1
                    searches_this_hour += 1
                    logger.info("[%s] cutoff searched %s %s", instance.name, item_type, item_id)

                if stop_pass:
                    break

                page += 1

    logger.info("[%s] cutoff pass complete — %d searched", instance.name, searched)
    return searched

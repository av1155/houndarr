"""Per-instance search loop.

:func:`run_instance_search` is the single entry point called by the supervisor.
It fetches one batch of missing items, applies cooldown and hourly-cap checks,
triggers the *arr search command for each eligible item, and writes a row to
``search_log`` for every item processed.
"""

from __future__ import annotations

import logging

from houndarr.clients.radarr import RadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.database import get_db
from houndarr.services.cooldown import (
    count_searches_last_hour,
    is_on_cooldown,
    record_search,
)
from houndarr.services.instances import Instance, InstanceType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# search_log helper
# ---------------------------------------------------------------------------


async def _write_log(
    instance_id: int | None,
    item_id: int | None,
    item_type: str | None,
    action: str,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    """Insert a single row into ``search_log``."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO search_log
                (instance_id, item_id, item_type, action, reason, message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (instance_id, item_id, item_type, action, reason, message),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_instance_search(instance: Instance, master_key: bytes) -> int:
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

    if instance.type == InstanceType.sonarr:
        client: SonarrClient | RadarrClient = SonarrClient(
            url=instance.url, api_key=instance.api_key
        )
        item_type = "episode"
    else:
        client = RadarrClient(url=instance.url, api_key=instance.api_key)
        item_type = "movie"

    async with client:
        items = await client.get_missing(page=1, page_size=instance.batch_size)

    logger.debug("[%s] fetched %d missing %s(s)", instance.name, len(items), item_type)

    for item in items:
        item_id: int = item.episode_id if item_type == "episode" else item.movie_id  # type: ignore[union-attr]

        # --- hourly cap check ---
        searches_this_hour = await count_searches_last_hour(instance.id)
        if instance.hourly_cap > 0 and searches_this_hour >= instance.hourly_cap:
            reason = f"hourly cap reached ({instance.hourly_cap})"
            logger.info("[%s] %s — %s", instance.name, item_id, reason)
            await _write_log(instance.id, item_id, item_type, "skipped", reason=reason)
            break

        # --- cooldown check ---
        if await is_on_cooldown(instance.id, item_id, item_type, instance.cooldown_days):  # type: ignore[arg-type]
            reason = f"on cooldown ({instance.cooldown_days}d)"
            logger.debug("[%s] %s — %s", instance.name, item_id, reason)
            await _write_log(instance.id, item_id, item_type, "skipped", reason=reason)
            continue

        # --- search ---
        try:
            async with client.__class__(url=instance.url, api_key=instance.api_key) as c:
                await c.search(item_id)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.warning("[%s] search failed for %s: %s", instance.name, item_id, msg)
            await _write_log(instance.id, item_id, item_type, "error", message=msg)
            continue

        await record_search(instance.id, item_id, item_type)  # type: ignore[arg-type]
        await _write_log(instance.id, item_id, item_type, "searched")
        searched += 1
        logger.info("[%s] searched %s %s", instance.name, item_type, item_id)

    logger.info("[%s] cycle complete — %d searched", instance.name, searched)
    return searched

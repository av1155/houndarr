"""Whisparr v2 adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.whisparr_v2.MissingWhisparrV2Episode` instances
into :class:`~houndarr.engine.candidates.SearchCandidate` and dispatches
search commands via :class:`~houndarr.clients.whisparr_v2.WhisparrV2Client`.

Whisparr v2 is a Sonarr fork, so the adapter structure mirrors the Sonarr
adapter with two key differences: the item type is ``whisparr_v2_episode``
(not shared with Sonarr), and episode labels omit ``episodeNumber`` (absent
in Whisparr v2).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError

from houndarr.clients._wire_models import ArrSeries
from houndarr.clients.base import InstanceSnapshot, ReconcileSets
from houndarr.clients.whisparr_v2 import (
    LibraryWhisparrV2Episode,
    MissingWhisparrV2Episode,
    WhisparrV2Client,
)
from houndarr.engine.adapters._common import (
    ContextOverride,
    build_cutoff_candidate,
    build_missing_candidate,
    compute_default_snapshot,
    paginate_wanted,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, WhisparrV2SearchMode

logger = logging.getLogger(__name__)

_UPGRADE_MAX_SERIES_PER_CYCLE = 5

# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


def _episode_label(item: MissingWhisparrV2Episode) -> str:
    """Build a human-readable log label for Whisparr v2 episodes.

    Whisparr v2 has no ``episodeNumber``; labels use season only.
    """
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - S{item.season_number:02d} - {item.episode_title}"
    return f"{series} - S{item.season_number:02d}"


def _season_context_label(item: MissingWhisparrV2Episode) -> str:
    """Build a log label for Whisparr v2 season-context search mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season_number:02d} (season-context)"


def _season_item_id(series_id: int, season_number: int) -> int:
    """Return a stable, negative synthetic ID representing a (series, season) pair.

    Uses the same scheme as the Sonarr adapter: ``-(series_id * 1000 + season_number)``.
    Collision with Sonarr is impossible because cooldowns and search_log are
    scoped by ``instance_id``.
    """
    return -(series_id * 1000 + season_number)


def _whisparr_v2_unreleased_reason(
    release_dt: datetime | None,
    grace_hrs: int,
) -> str | None:
    """Return skip reason when a Whisparr v2 episode is not yet searchable.

    Whisparr v2 provides a parsed ``datetime`` (from DateOnly) rather than
    an ISO-8601 string, so this helper operates on ``datetime`` directly.
    """
    if release_dt is None:
        return None
    now = datetime.now(UTC)
    if now < release_dt:
        return "not yet released"
    if grace_hrs > 0 and now < (release_dt + timedelta(hours=grace_hrs)):
        return f"post-release grace ({grace_hrs}h)"
    return None


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingWhisparrV2Episode, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr v2 missing episode into a :class:`SearchCandidate`.

    Args:
        item: A missing episode returned by :meth:`WhisparrV2Client.get_missing`.
        instance: The configured Whisparr v2 instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason = _whisparr_v2_unreleased_reason(
        item.release_date, instance.missing.post_release_grace_hrs
    )

    # Episodes without any series linkage (series_id is None means both
    # seriesId and series.id were absent from the API response) are orphan
    # records that Whisparr v2 cannot reliably search. Skip them so the
    # pipeline logs a clean "skipped" row instead of a dispatch-then-fail
    # "error" row.  Season-0 specials with a valid series_id are unaffected.
    if item.series_id is None and unreleased_reason is None:
        unreleased_reason = "no series linked"

    context: ContextOverride | None = None
    if (
        instance.missing.whisparr_v2_search_mode != WhisparrV2SearchMode.episode
        and item.series_id is not None
        and item.season_number > 0
    ):
        context = ContextOverride(
            item_id=_season_item_id(item.series_id, item.season_number),
            label=_season_context_label(item),
            group_key=(item.series_id, item.season_number),
            search_payload={
                "command": "SeasonSearch",
                "series_id": item.series_id,
                "season_number": item.season_number,
            },
        )

    return build_missing_candidate(
        item_type="whisparr_v2_episode",
        item_id=item.episode_id,
        label=_episode_label(item),
        unreleased_reason=unreleased_reason,
        search_payload={
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        },
        context=context,
    )


def adapt_cutoff(item: MissingWhisparrV2Episode, instance: Instance) -> SearchCandidate:
    """Convert a Whisparr v2 cutoff-unmet episode into a :class:`SearchCandidate`.

    Cutoff always uses episode-mode regardless of ``whisparr_v2_search_mode``.

    Args:
        item: A cutoff-unmet episode from :meth:`WhisparrV2Client.get_cutoff_unmet`.
        instance: The configured Whisparr v2 instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason = _whisparr_v2_unreleased_reason(
        item.release_date, instance.missing.post_release_grace_hrs
    )

    # Same orphan guard as adapt_missing: skip records with no series linkage.
    if item.series_id is None and unreleased_reason is None:
        unreleased_reason = "no series linked"

    return build_cutoff_candidate(
        item_type="whisparr_v2_episode",
        item_id=item.episode_id,
        label=_episode_label(item),
        unreleased_reason=unreleased_reason,
        search_payload={
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        },
    )


def _library_episode_label(item: LibraryWhisparrV2Episode) -> str:
    """Build a human-readable log label for library Whisparr v2 episodes."""
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - S{item.season_number:02d} - {item.episode_title}"
    return f"{series} - S{item.season_number:02d}"


def _library_season_context_label(item: LibraryWhisparrV2Episode) -> str:
    """Build a log label for library Whisparr v2 episode in season-context mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season_number:02d} (season-context)"


def adapt_upgrade(
    item: LibraryWhisparrV2Episode,
    instance: Instance,
) -> SearchCandidate:
    """Convert a Whisparr v2 library episode into a :class:`SearchCandidate` for upgrade.

    Respects ``instance.upgrade.upgrade_whisparr_v2_search_mode`` for episode
    vs season-context.  No unreleased checks: upgrade items already have files.

    Args:
        item: A library episode from :meth:`WhisparrV2Client.get_episodes`.
        instance: The configured Whisparr v2 instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    episode_mode = instance.upgrade.upgrade_whisparr_v2_search_mode == WhisparrV2SearchMode.episode

    use_season_context = not episode_mode and item.series_id > 0 and item.season_number > 0

    if use_season_context:
        item_id = _season_item_id(item.series_id, item.season_number)
        label = _library_season_context_label(item)
        group_key: tuple[int, int] | None = (item.series_id, item.season_number)
        search_payload: dict[str, Any] = {
            "command": "SeasonSearch",
            "series_id": item.series_id,
            "season_number": item.season_number,
        }
    else:
        item_id = item.episode_id
        label = _library_episode_label(item)
        group_key = None
        search_payload = {
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        }

    return SearchCandidate(
        item_id=item_id,
        item_type="whisparr_v2_episode",
        label=label,
        unreleased_reason=None,
        group_key=group_key,
        search_payload=search_payload,
    )


async def _collect_upgrade_episodes(
    client: WhisparrV2Client,
    instance: Instance,
    series_list: Sequence[ArrSeries],
) -> list[LibraryWhisparrV2Episode]:
    """Return monitored, on-disk, cutoff-met episodes for the given series.

    Shared loop body between the rotation-windowed search-cycle fetch
    (:func:`fetch_upgrade_pool`) and the full-library reconcile fetch
    (:func:`_fetch_all_upgrade_episodes`).  Transport or validation
    errors on a single series are logged and skipped so one flaky
    series cannot blank the whole pool.
    """
    episodes: list[LibraryWhisparrV2Episode] = []
    for s in series_list:
        series_id = s.id or 0
        try:
            eps = await client.get_episodes(series_id)
        except (httpx.HTTPError, httpx.InvalidURL, ValidationError):
            logger.warning(
                "[%s] failed to fetch episodes for series %d, skipping",
                instance.core.name,
                series_id,
            )
            continue
        episodes.extend(e for e in eps if e.monitored and e.has_file and e.cutoff_met)
    return episodes


async def fetch_upgrade_pool(
    client: WhisparrV2Client,
    instance: Instance,
) -> list[LibraryWhisparrV2Episode]:
    """Fetch and filter Whisparr v2 library for upgrade-eligible episodes.

    Uses series rotation: fetches up to ``_UPGRADE_MAX_SERIES_PER_CYCLE``
    monitored series per cycle, starting from ``instance.upgrade.upgrade_series_offset``.

    Args:
        client: An open :class:`WhisparrV2Client` context.
        instance: The configured Whisparr v2 instance.

    Returns:
        List of upgrade-eligible :class:`LibraryWhisparrV2Episode` items.
    """
    all_series = await client.get_series()
    monitored = sorted(
        [s for s in all_series if s.monitored],
        key=lambda s: s.id or 0,
    )

    if not monitored:
        return []

    offset = instance.upgrade.upgrade_series_offset % len(monitored)
    # Per-instance window size lets users with very large libraries trade
    # higher per-cycle *arr load for faster rotation coverage.  Clamp to
    # at least 1 so a stored 0 (impossible per CHECK constraint, but
    # defensive) still makes progress.
    window = max(1, instance.upgrade.upgrade_series_window_size)
    selected = monitored[offset : offset + window]
    if len(selected) < window:
        remaining = window - len(selected)
        selected += monitored[:remaining]

    return await _collect_upgrade_episodes(client, instance, selected)


async def _fetch_all_upgrade_episodes(
    client: WhisparrV2Client,
    instance: Instance,
) -> list[LibraryWhisparrV2Episode]:
    """Return upgrade-eligible episodes across EVERY monitored series.

    The cycle-facing :func:`fetch_upgrade_pool` windows the series list
    via ``upgrade_series_offset`` for per-cycle politeness.  Reconcile
    cannot use that window: anything outside the current slice would
    be flagged as an orphan and deleted on the next snapshot refresh,
    collapsing ``upgrade_cooldown_days`` to one rotation period.  This
    helper walks every monitored series so the reconcile upgrade set
    matches the universe :func:`adapt_upgrade` could legitimately
    stamp.  The N extra episode fetches are amortised across the
    10-minute supervisor cadence.
    """
    all_series = await client.get_series()
    monitored = [s for s in all_series if s.monitored]
    return await _collect_upgrade_episodes(client, instance, monitored)


async def dispatch_search(client: WhisparrV2Client, candidate: SearchCandidate) -> None:
    """Dispatch the appropriate Whisparr v2 search command for *candidate*.

    Args:
        client: An open :class:`WhisparrV2Client` context.
        candidate: The candidate to search for.

    Raises:
        ValueError: If ``search_payload["command"]`` is unrecognised.
    """
    command = candidate.search_payload["command"]
    if command == "SeasonSearch":
        await client.search_season(
            candidate.search_payload["series_id"],
            candidate.search_payload["season_number"],
        )
    elif command == "EpisodeSearch":
        await client.search(candidate.search_payload["episode_id"])
    else:
        msg = f"Unknown Whisparr v2 search command: {command}"
        raise ValueError(msg)


def make_client(instance: Instance) -> WhisparrV2Client:
    """Construct a :class:`WhisparrV2Client` for *instance*.

    Args:
        instance: The configured Whisparr v2 instance.

    Returns:
        A new (unopened) :class:`WhisparrV2Client`.
    """
    return WhisparrV2Client(url=instance.core.url, api_key=instance.core.api_key)


def _whisparr_v2_leaf_pairs(items: list[MissingWhisparrV2Episode]) -> frozenset[tuple[str, int]]:
    """Return ``(item_type, episode_id)`` pairs for a wanted list.

    Whisparr v2 stamps cooldowns with ``item_type="whisparr_v2_episode"``
    to distinguish them from Sonarr episodes that share the same
    numeric id space.
    """
    return frozenset(("whisparr_v2_episode", it.episode_id) for it in items if it.episode_id)


def _whisparr_v2_season_synth_pairs(
    items: list[MissingWhisparrV2Episode],
) -> frozenset[tuple[str, int]]:
    """Return synthetic season-context pairs for Whisparr v2 cooldowns."""
    parents: set[tuple[int, int]] = set()
    for it in items:
        if it.series_id is not None and it.series_id > 0 and it.season_number > 0:
            parents.add((it.series_id, it.season_number))
    return frozenset(("whisparr_v2_episode", _season_item_id(sid, sn)) for sid, sn in parents)


async def fetch_reconcile_sets(client: WhisparrV2Client, instance: Instance) -> ReconcileSets:
    """Return the authoritative wanted / upgrade-pool sets for Whisparr v2.

    Mirrors the Sonarr implementation but uses the
    ``whisparr_v2_episode`` item_type.  In ``season_context`` missing-pass
    mode, synthetic negative season ids derived from the same wanted
    list are unioned in.  Cutoff cooldowns stay leaf-only.  The
    upgrade set walks the FULL monitored library via
    :func:`_fetch_all_upgrade_episodes` rather than the cycle-rotation
    window so ``upgrade_cooldown_days`` is not silently collapsed to
    one rotation period.  An upgrade-disabled instance short-circuits
    to an empty upgrade set so no library traffic is paid.
    """
    missing_items = await paginate_wanted(client.get_missing)
    cutoff_items = await paginate_wanted(client.get_cutoff_unmet)
    missing_set = _whisparr_v2_leaf_pairs(missing_items)
    cutoff_set = _whisparr_v2_leaf_pairs(cutoff_items)
    if instance.missing.whisparr_v2_search_mode != WhisparrV2SearchMode.episode:
        missing_set = missing_set | _whisparr_v2_season_synth_pairs(missing_items)
    upgrade_set: frozenset[tuple[str, int]] = frozenset()
    if instance.upgrade.upgrade_enabled:
        upgrade_candidates = [
            adapt_upgrade(item, instance)
            for item in await _fetch_all_upgrade_episodes(client, instance)
        ]
        upgrade_set = frozenset((str(c.item_type), c.item_id) for c in upgrade_candidates)
    return ReconcileSets(missing=missing_set, cutoff=cutoff_set, upgrade=upgrade_set)


async def fetch_instance_snapshot(
    client: WhisparrV2Client,
    instance: Instance,  # noqa: ARG001
) -> InstanceSnapshot:
    """Compose the dashboard snapshot for a Whisparr v2 instance.

    Whisparr v2 is the only adapter whose domain model holds a
    pre-parsed ``datetime`` (the wire field accepts both ISO strings
    and ``{year, month, day}`` dicts; :func:`_parse_date_only` in the
    client normalises both to a single shape).  The shared snapshot
    helper takes the dt-typed branch via ``anchor_is_dt=True`` so the
    re-parse step is skipped.
    """
    return await compute_default_snapshot(
        client,
        anchor_fn=lambda ep: ep.release_date,
        anchor_is_dt=True,
    )


class WhisparrV2Adapter:
    """Class-form Whisparr v2 adapter for the :data:`ADAPTERS` registry.

    Conforms to :class:`~houndarr.engine.adapters.protocols.AppAdapterProto`
    structurally via the eight staticmethod attributes below; the
    module-level functions remain importable for direct unit-test use.
    """

    adapt_missing = staticmethod(adapt_missing)
    adapt_cutoff = staticmethod(adapt_cutoff)
    adapt_upgrade = staticmethod(adapt_upgrade)
    fetch_upgrade_pool = staticmethod(fetch_upgrade_pool)
    dispatch_search = staticmethod(dispatch_search)
    make_client = staticmethod(make_client)
    fetch_reconcile_sets = staticmethod(fetch_reconcile_sets)
    fetch_instance_snapshot = staticmethod(fetch_instance_snapshot)

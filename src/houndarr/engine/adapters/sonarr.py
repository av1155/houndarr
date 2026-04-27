"""Sonarr adapter functions for the search engine pipeline.

Converts :class:`~houndarr.clients.sonarr.MissingEpisode` instances into
:class:`~houndarr.engine.candidates.SearchCandidate` and dispatches search
commands via :class:`~houndarr.clients.sonarr.SonarrClient`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from houndarr.clients._wire_models import ArrSeries
from houndarr.clients.base import InstanceSnapshot, ReconcileSets
from houndarr.clients.sonarr import LibraryEpisode, MissingEpisode, SonarrClient
from houndarr.engine.adapters._common import (
    ContextOverride,
    build_cutoff_candidate,
    build_missing_candidate,
    compute_default_snapshot,
    paginate_wanted,
)
from houndarr.engine.candidates import (
    SearchCandidate,
    _is_unreleased,
    _is_within_post_release_grace,
)
from houndarr.services.instances import Instance, SonarrSearchMode

logger = logging.getLogger(__name__)

_UPGRADE_MAX_SERIES_PER_CYCLE = 5

# ---------------------------------------------------------------------------
# Label builders (copied from search_loop.py; originals removed in Phase 2)
# ---------------------------------------------------------------------------


def _episode_label(item: MissingEpisode) -> str:
    """Build a human-readable log label for Sonarr episodes."""
    code = f"S{item.season:02d}E{item.episode:02d}"
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - {code} - {item.episode_title}"
    return f"{series} - {code}"


def _season_context_label(item: MissingEpisode) -> str:
    """Build a log label for Sonarr season-context search mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season:02d} (season-context)"


def _season_item_id(series_id: int, season_number: int) -> int:
    """Return a stable, negative synthetic ID representing a (series, season) pair.

    Season-context searches must be keyed on a season-level identity rather than
    an individual episode ID so that cooldown and log history remain consistent
    across cycles regardless of which episode happens to be the first candidate.

    The scheme encodes ``series_id`` and ``season_number`` as a single negative
    integer: ``-(series_id * 1000 + season_number)``.  Sonarr episode IDs are
    always positive, so there is no collision risk with real episode IDs stored
    in the same ``cooldowns``/``search_log`` tables.

    The multiplier 1000 supports up to 999 seasons per series, which exceeds
    any realistic Sonarr library.

    Args:
        series_id: Sonarr series ID (positive integer).
        season_number: Season number (0-based specials supported; positive for
            regular seasons).

    Returns:
        A unique negative integer that identifies this season across all cycles.
    """
    return -(series_id * 1000 + season_number)


def _sonarr_unreleased_reason(air_date_utc: str | None, grace_hrs: int) -> str | None:
    """Return skip reason when an episode should be treated as not yet searchable."""
    if _is_unreleased(air_date_utc):
        return "not yet released"
    if _is_within_post_release_grace(air_date_utc, grace_hrs):
        return f"post-release grace ({grace_hrs}h)"
    return None


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------


def adapt_missing(item: MissingEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Sonarr missing episode into a :class:`SearchCandidate`.

    Replicates the branching logic from ``search_loop.py`` for episode-mode
    versus season-context mode, including synthetic season ID generation,
    label selection, and unreleased-delay checking.

    Args:
        item: A missing episode returned by :meth:`SonarrClient.get_missing`.
        instance: The configured Sonarr instance (provides search-mode and
            unreleased-delay settings).

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    unreleased_reason = _sonarr_unreleased_reason(
        item.air_date_utc, instance.missing.post_release_grace_hrs
    )

    context: ContextOverride | None = None
    if (
        instance.missing.sonarr_search_mode != SonarrSearchMode.episode
        and item.series_id is not None
        and item.season > 0
    ):
        context = ContextOverride(
            item_id=_season_item_id(item.series_id, item.season),
            label=_season_context_label(item),
            group_key=(item.series_id, item.season),
            search_payload={
                "command": "SeasonSearch",
                "series_id": item.series_id,
                "season_number": item.season,
            },
        )

    return build_missing_candidate(
        item_type="episode",
        item_id=item.episode_id,
        label=_episode_label(item),
        unreleased_reason=unreleased_reason,
        search_payload={
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        },
        context=context,
    )


def adapt_cutoff(item: MissingEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Sonarr cutoff-unmet episode into a :class:`SearchCandidate`.

    The cutoff pass always uses episode-mode regardless of
    ``instance.missing.sonarr_search_mode``, matching the current behavior in
    ``search_loop.py``.

    Args:
        item: A cutoff-unmet episode from :meth:`SonarrClient.get_cutoff_unmet`.
        instance: The configured Sonarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    return build_cutoff_candidate(
        item_type="episode",
        item_id=item.episode_id,
        label=_episode_label(item),
        unreleased_reason=_sonarr_unreleased_reason(
            item.air_date_utc, instance.missing.post_release_grace_hrs
        ),
        search_payload={
            "command": "EpisodeSearch",
            "episode_id": item.episode_id,
        },
    )


def _library_episode_label(item: LibraryEpisode) -> str:
    """Build a human-readable log label for library episodes."""
    code = f"S{item.season:02d}E{item.episode:02d}"
    series = item.series_title or "Unknown Series"
    if item.episode_title:
        return f"{series} - {code} - {item.episode_title}"
    return f"{series} - {code}"


def _library_season_context_label(item: LibraryEpisode) -> str:
    """Build a log label for library episode in season-context mode."""
    series = item.series_title or "Unknown Series"
    return f"{series} - S{item.season:02d} (season-context)"


def adapt_upgrade(item: LibraryEpisode, instance: Instance) -> SearchCandidate:
    """Convert a Sonarr library episode into a :class:`SearchCandidate` for upgrade.

    Respects ``instance.upgrade.upgrade_sonarr_search_mode`` for episode vs season-context.
    No unreleased checks: upgrade items already have files.

    Args:
        item: A library episode from :meth:`SonarrClient.get_episodes`.
        instance: The configured Sonarr instance.

    Returns:
        A fully populated :class:`SearchCandidate`.
    """
    episode_mode = instance.upgrade.upgrade_sonarr_search_mode == SonarrSearchMode.episode

    use_season_context = not episode_mode and item.series_id > 0 and item.season > 0

    if use_season_context:
        item_id = _season_item_id(item.series_id, item.season)
        label = _library_season_context_label(item)
        group_key: tuple[int, int] | None = (item.series_id, item.season)
        search_payload: dict[str, Any] = {
            "command": "SeasonSearch",
            "series_id": item.series_id,
            "season_number": item.season,
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
        item_type="episode",
        label=label,
        unreleased_reason=None,
        group_key=group_key,
        search_payload=search_payload,
    )


async def _collect_upgrade_episodes(
    client: SonarrClient,
    instance: Instance,
    series_list: Sequence[ArrSeries],
) -> list[LibraryEpisode]:
    """Return monitored, on-disk, cutoff-met episodes for the given series.

    Shared loop body between the rotation-windowed search-cycle fetch
    (:func:`fetch_upgrade_pool`) and the full-library reconcile fetch
    (:func:`_fetch_all_upgrade_episodes`).  Transport or validation
    errors on a single series are logged and skipped so one flaky
    series cannot blank the whole pool.
    """
    episodes: list[LibraryEpisode] = []
    for s in series_list:
        series_id = s.id or 0
        try:
            eps = await client.get_episodes(series_id)
        except (httpx.HTTPError, httpx.InvalidURL, ValidationError):
            logger.warning(
                "[%s] failed to fetch episodes for series %d, skipping",
                instance.name,
                series_id,
            )
            continue
        episodes.extend(e for e in eps if e.monitored and e.has_file and e.cutoff_met)
    return episodes


async def fetch_upgrade_pool(
    client: SonarrClient,
    instance: Instance,
) -> list[LibraryEpisode]:
    """Fetch and filter Sonarr library for upgrade-eligible episodes.

    Uses series rotation: fetches up to
    ``instance.upgrade.upgrade_series_window_size`` monitored series per
    cycle (default 5; capped at the module fallback for safety), starting
    from ``instance.upgrade.upgrade_series_offset``.

    Args:
        client: An open :class:`SonarrClient` context.
        instance: The configured Sonarr instance.

    Returns:
        List of upgrade-eligible :class:`LibraryEpisode` items.
    """
    all_series = await client.get_series()
    monitored = sorted(
        [s for s in all_series if s.monitored],
        key=lambda s: s.id or 0,
    )

    if not monitored:
        return []

    # Per-instance window size lets users with very large libraries trade
    # higher per-cycle *arr load for faster rotation coverage.  Clamp to
    # at least 1 so a stored 0 (impossible per CHECK constraint, but
    # defensive) still makes progress.
    window = max(1, instance.upgrade.upgrade_series_window_size)
    offset = instance.upgrade.upgrade_series_offset % len(monitored)
    selected = monitored[offset : offset + window]
    if len(selected) < window:
        remaining = window - len(selected)
        selected += monitored[:remaining]

    return await _collect_upgrade_episodes(client, instance, selected)


async def _fetch_all_upgrade_episodes(
    client: SonarrClient,
    instance: Instance,
) -> list[LibraryEpisode]:
    """Return upgrade-eligible episodes across EVERY monitored series.

    The cycle-facing :func:`fetch_upgrade_pool` windows the series list
    via ``upgrade_series_offset`` so indexer traffic stays polite per
    cycle.  Reconciliation cannot use that windowed set: it would mark
    every upgrade cooldown outside the current slice as an orphan and
    delete it on the next snapshot refresh, collapsing the configured
    ``upgrade_cooldown_days`` down to roughly one rotation period.
    This helper ignores the window and walks the full monitored list
    so the reconcile upgrade set matches everything
    :func:`adapt_upgrade` could legitimately stamp.  Amortised against
    the supervisor's 10-minute snapshot cadence.
    """
    all_series = await client.get_series()
    monitored = [s for s in all_series if s.monitored]
    return await _collect_upgrade_episodes(client, instance, monitored)


async def dispatch_search(client: SonarrClient, candidate: SearchCandidate) -> None:
    """Dispatch the appropriate Sonarr search command for *candidate*.

    Reads ``candidate.search_payload["command"]`` to decide between
    ``EpisodeSearch`` and ``SeasonSearch``.

    Args:
        client: An open :class:`SonarrClient` context.
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
        msg = f"Unknown Sonarr search command: {command}"
        raise ValueError(msg)


def make_client(instance: Instance) -> SonarrClient:
    """Construct a :class:`SonarrClient` for *instance*.

    Args:
        instance: The configured Sonarr instance.

    Returns:
        A new (unopened) :class:`SonarrClient`.
    """
    return SonarrClient(url=instance.core.url, api_key=instance.core.api_key)


def _episode_leaf_pairs(items: list[MissingEpisode]) -> frozenset[tuple[str, int]]:
    """Return the ``(item_type, episode_id)`` pairs for a wanted list.

    Shared between the missing and cutoff passes; leaf cooldown rows
    match episodes by positive episode_id.  Items whose ``episode_id``
    is missing are dropped (defensive: the Sonarr wire model marks it
    as ``int`` not optional, so this should not fire in practice).
    """
    return frozenset(("episode", it.episode_id) for it in items if it.episode_id)


def _season_synth_pairs(items: list[MissingEpisode]) -> frozenset[tuple[str, int]]:
    """Return ``(item_type, synthetic)`` pairs for season-context rows.

    Collapses the leaf wanted list to the set of ``(series_id,
    season)`` parents, then renders each parent through
    :func:`_season_item_id` so the synthetic negative ids match what
    :func:`adapt_missing` stamps onto cooldowns in season-context
    mode.  Items without ``series_id`` are skipped; they could not
    have produced a season-context cooldown in the first place.
    """
    parents: set[tuple[int, int]] = set()
    for it in items:
        if it.series_id:
            parents.add((it.series_id, it.season))
    return frozenset(("episode", _season_item_id(sid, sn)) for sid, sn in parents)


async def fetch_reconcile_sets(client: SonarrClient, instance: Instance) -> ReconcileSets:
    """Return the authoritative wanted / upgrade-pool sets for Sonarr.

    Always unions leaf episode ids into the missing / cutoff sets.
    When the instance runs in ``season_context`` missing-pass mode, the
    missing set ALSO carries synthetic negative season ids derived
    from the same wanted list so cooldown rows stamped under the
    season-context path keep matching.  Cutoff is always dispatched
    per-episode, so cutoff cooldown rows never carry synthetic ids;
    the cutoff reconcile set stays leaf-only.  The upgrade set walks
    the FULL monitored library via :func:`_fetch_all_upgrade_episodes`
    rather than the cycle-rotation window, otherwise reconcile would
    treat every upgrade cooldown outside the current 5-series slice as
    an orphan and truncate ``upgrade_cooldown_days`` to one rotation.
    When ``upgrade_enabled`` is false the upgrade set is an empty
    frozenset so no library traffic is paid; no upgrade cooldowns can
    exist in that state anyway.
    """
    missing_items = await paginate_wanted(client.get_missing)
    cutoff_items = await paginate_wanted(client.get_cutoff_unmet)
    missing_set = _episode_leaf_pairs(missing_items)
    cutoff_set = _episode_leaf_pairs(cutoff_items)
    if instance.sonarr_search_mode != SonarrSearchMode.episode:
        missing_set = missing_set | _season_synth_pairs(missing_items)
    upgrade_set: frozenset[tuple[str, int]] = frozenset()
    if instance.upgrade_enabled:
        upgrade_candidates = [
            adapt_upgrade(item, instance)
            for item in await _fetch_all_upgrade_episodes(client, instance)
        ]
        upgrade_set = frozenset((str(c.item_type), c.item_id) for c in upgrade_candidates)
    return ReconcileSets(missing=missing_set, cutoff=cutoff_set, upgrade=upgrade_set)


async def fetch_instance_snapshot(
    client: SonarrClient,
    instance: Instance,  # noqa: ARG001
) -> InstanceSnapshot:
    """Compose the dashboard snapshot for a Sonarr instance.

    Anchor for unreleased detection is :attr:`MissingEpisode.air_date_utc`
    (single ISO string).  Episodes without an air date fall through to
    the "already released" branch in :func:`_is_unreleased`, matching
    the search-loop's classification — Sonarr-without-air-date is not
    something Houndarr should flag as pre-release on the dashboard.
    """
    return await compute_default_snapshot(
        client,
        anchor_fn=lambda ep: ep.air_date_utc,
    )


class SonarrAdapter:
    """Class-form Sonarr adapter for the :data:`ADAPTERS` registry.

    Conforms to :class:`~houndarr.engine.adapters.protocols.AppAdapterProto`
    structurally via the eight staticmethod attributes below; the
    module-level functions remain importable for direct unit-test use.
    Track C.10 introduces this class form to replace the prior
    ``AppAdapter`` dataclass-of-callables registry shape.
    """

    adapt_missing = staticmethod(adapt_missing)
    adapt_cutoff = staticmethod(adapt_cutoff)
    adapt_upgrade = staticmethod(adapt_upgrade)
    fetch_upgrade_pool = staticmethod(fetch_upgrade_pool)
    dispatch_search = staticmethod(dispatch_search)
    make_client = staticmethod(make_client)
    fetch_reconcile_sets = staticmethod(fetch_reconcile_sets)
    fetch_instance_snapshot = staticmethod(fetch_instance_snapshot)

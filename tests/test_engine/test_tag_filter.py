"""Unit tests for the per-instance tag-based include / exclude filter.

Issue #637.  Covers the small helpers (``_tag_filter_skip_reason`` and
``_resolve_tag_filter_ids``) in isolation so the per-cycle filter behaviour
is pinned independent of the full ``_run_search_pass`` integration.

The end-to-end search-pass tests live in ``test_search_loop.py`` and
exercise the wired-up engine; this file is the property-shaped pin for
the filter logic alone.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from houndarr.engine.candidates import ItemType, SearchCandidate
from houndarr.engine.search_loop import (
    _resolve_tag_filter_ids,
    _tag_filter_skip_reason,
)
from houndarr.enums import SearchKind
from houndarr.errors import ClientTransportError
from houndarr.services.instances import TagFilterPolicy


def _make_candidate(tags: tuple[int, ...] = ()) -> SearchCandidate:
    """Build a minimal :class:`SearchCandidate` for filter assertions."""
    return SearchCandidate(
        item_id=1,
        item_type=ItemType.movie,
        label="Fixture",
        unreleased_reason=None,
        group_key=None,
        search_payload={"command": "MoviesSearch", "movie_id": 1},
        tags=tags,
    )


class TestTagFilterPolicyForPass:
    """``TagFilterPolicy.for_pass`` picks the labels each pass filters on.

    Issue #833.  A per-pass field overrides the instance-wide field of
    the same direction; a blank per-pass field falls back to it.  The
    two directions fall back independently.
    """

    _POLICY = TagFilterPolicy(
        include=("all-in",),
        exclude=("all-out",),
        missing_include=("m-in",),
        cutoff_exclude=("c-out",),
        upgrade_include=("u-in",),
        upgrade_exclude=("u-out",),
    )

    @pytest.mark.parametrize(
        ("kind", "want"),
        [
            (SearchKind.missing, (("m-in",), ("all-out",))),
            (SearchKind.cutoff, (("all-in",), ("c-out",))),
            (SearchKind.upgrade, (("u-in",), ("u-out",))),
        ],
    )
    def test_override_replaces_instance_wide_per_direction(
        self, kind: SearchKind, want: tuple[tuple[str, ...], tuple[str, ...]]
    ) -> None:
        assert self._POLICY.for_pass(kind) == want

    @pytest.mark.parametrize("kind", list(SearchKind))
    def test_no_overrides_uses_instance_wide_on_every_pass(self, kind: SearchKind) -> None:
        """Rows migrated to v22 carry blank overrides and must filter as before."""
        policy = TagFilterPolicy(include=("1080p",), exclude=("uncut",))
        assert policy.for_pass(kind) == (("1080p",), ("uncut",))

    def test_override_with_blank_instance_wide(self) -> None:
        """Filtering one pass only: instance-wide blank, override on that pass."""
        policy = TagFilterPolicy(cutoff_exclude=("no-upgrade",))
        assert policy.for_pass(SearchKind.missing) == ((), ())
        assert policy.for_pass(SearchKind.cutoff) == ((), ("no-upgrade",))
        assert policy.for_pass(SearchKind.upgrade) == ((), ())


class TestTagFilterSkipReason:
    """``_tag_filter_skip_reason`` decides per-candidate pass / fail."""

    def test_no_filter_passes_every_candidate(self) -> None:
        """Both directions ``None`` means the filter is a no-op."""
        assert _tag_filter_skip_reason(_make_candidate(), None, None) is None
        assert _tag_filter_skip_reason(_make_candidate(tags=(1, 2)), None, None) is None

    def test_include_match_passes(self) -> None:
        """A candidate with at least one included tag passes."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(1, 5)),
            frozenset({1, 9}),
            None,
        )
        assert result is None

    def test_include_no_match_fails(self) -> None:
        """A candidate with no matching include tag skips with the include reason."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(7, 8)),
            frozenset({1, 2}),
            None,
        )
        assert result == "tag filter (no included tag)"

    def test_zero_tags_fails_include_filter(self) -> None:
        """Items with no tags cannot match any include tag."""
        result = _tag_filter_skip_reason(_make_candidate(tags=()), frozenset({1}), None)
        assert result == "tag filter (no included tag)"

    def test_empty_include_set_fails(self) -> None:
        """An include set that resolved to zero IDs (all labels unknown) skips
        every candidate; otherwise an operator typo silently bypasses the
        filter, which is the wrong default for a safety control."""
        result = _tag_filter_skip_reason(_make_candidate(tags=(1, 2)), frozenset(), None)
        assert result == "tag filter (no included tag)"

    def test_exclude_match_fails(self) -> None:
        """A candidate with any excluded tag skips with the exclude reason."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(1, 2)),
            None,
            frozenset({2}),
        )
        assert result == "tag filter (excluded tag)"

    def test_exclude_no_match_passes(self) -> None:
        """Candidates with no excluded tag pass even when the exclude set is set."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(7, 8)),
            None,
            frozenset({1, 2}),
        )
        assert result is None

    def test_zero_tags_passes_exclude_filter(self) -> None:
        """Untagged items have nothing to exclude on."""
        result = _tag_filter_skip_reason(_make_candidate(tags=()), None, frozenset({1, 2}))
        assert result is None

    def test_include_evaluated_before_exclude(self) -> None:
        """When both filters apply, include is checked first.

        A candidate that fails include should report the include reason
        rather than the exclude reason, even if the exclude filter would
        also block it.  This is the contract the engine relies on for
        consistent skip-log breakdowns.
        """
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(9,)),
            frozenset({1, 2}),
            frozenset({9}),
        )
        assert result == "tag filter (no included tag)"

    def test_both_filters_pass(self) -> None:
        """Tag in include set, no tag in exclude set: candidate passes."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(1,)),
            frozenset({1}),
            frozenset({99}),
        )
        assert result is None


class TestResolveTagFilterIds:
    """``_resolve_tag_filter_ids`` runs once per cycle.

    The function takes the instance's labels, calls the adapter's client
    ``get_tags()`` once, and returns the resolved sets for each pass.  These tests use
    a minimal stub adapter plus a real :class:`TagFilterPolicy` so the
    label normalisation + degradation behaviour is locked.
    """

    @pytest.fixture()
    def instance(self) -> Any:
        from tests.test_engine.conftest import make_instance  # noqa: PLC0415

        return make_instance()

    def _stub_adapter(self, *, get_tags_result: Any = None, raises: Any = None) -> Any:
        """Build a minimal adapter whose ``make_client`` yields a stub client.

        ``get_tags_result`` is the dict the client returns; ``raises`` is
        an exception to throw instead (for the failure-mode test).
        ``make_client`` is wired as a ``MagicMock`` (not a lambda) so the
        short-circuit assertions in the no-filter test can inspect call
        history.
        """
        from unittest.mock import MagicMock  # noqa: PLC0415

        client = AsyncMock()
        if raises is not None:
            client.get_tags = AsyncMock(side_effect=raises)
        else:
            client.get_tags = AsyncMock(return_value=get_tags_result or {})

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=None)

        adapter = AsyncMock()
        adapter.make_client = MagicMock(return_value=ctx)
        return adapter

    @pytest.fixture()
    def all_passes_instance(self) -> Any:
        from tests.test_engine.conftest import make_instance  # noqa: PLC0415

        return make_instance(cutoff_enabled=True, upgrade_enabled=True)

    _ALL_OFF = {
        SearchKind.missing: (None, None),
        SearchKind.cutoff: (None, None),
        SearchKind.upgrade: (None, None),
    }

    @pytest.mark.asyncio()
    async def test_empty_filter_short_circuits(self, instance: Any, seeded_instances: None) -> None:
        """With no labels on any pass every pass gets ``(None, None)``
        without opening a client (and therefore without firing the /tag GET)."""
        adapter = self._stub_adapter()
        tag_ids = await _resolve_tag_filter_ids(
            instance, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert tag_ids == self._ALL_OFF
        adapter.make_client.assert_not_called()

    @pytest.mark.asyncio()
    async def test_filter_only_on_disabled_pass_skips_get(
        self, instance: Any, seeded_instances: None
    ) -> None:
        """An override on a pass that will not run must not cost a /tag GET."""
        import dataclasses  # noqa: PLC0415

        scoped = dataclasses.replace(
            instance,
            tag_filter=TagFilterPolicy(upgrade_exclude=("no-upgrade",)),
        )
        adapter = self._stub_adapter(get_tags_result={"no-upgrade": 5})
        tag_ids = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert tag_ids == self._ALL_OFF
        adapter.make_client.assert_not_called()

    @pytest.mark.asyncio()
    async def test_per_pass_overrides_share_one_get(
        self, all_passes_instance: Any, seeded_instances: None
    ) -> None:
        """Issue #833.  Each pass resolves its own effective labels from a
        single /tag GET per cycle."""
        import dataclasses  # noqa: PLC0415

        scoped = dataclasses.replace(
            all_passes_instance,
            tag_filter=TagFilterPolicy(
                exclude=("all-out",),
                cutoff_exclude=("no-upgrade",),
                upgrade_include=("4k",),
                upgrade_exclude=("no-upgrade",),
            ),
        )
        adapter = self._stub_adapter(get_tags_result={"all-out": 3, "no-upgrade": 5, "4k": 2})
        tag_ids = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert tag_ids == {
            SearchKind.missing: (None, frozenset({3})),
            SearchKind.cutoff: (None, frozenset({5})),
            SearchKind.upgrade: (frozenset({2}), frozenset({5})),
        }
        client = adapter.make_client.return_value.__aenter__.return_value
        client.get_tags.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_unknown_label_logged_once_across_passes(
        self, all_passes_instance: Any, seeded_instances: None
    ) -> None:
        """An instance-wide typo reaches every pass but is reported once."""
        import dataclasses  # noqa: PLC0415

        from houndarr.database import get_db  # noqa: PLC0415

        scoped = dataclasses.replace(
            all_passes_instance,
            tag_filter=TagFilterPolicy(include=("nope",), cutoff_exclude=("nope",)),
        )
        adapter = self._stub_adapter(get_tags_result={"1080p": 1})
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        async with get_db() as conn:
            async with conn.execute(
                "SELECT message FROM search_log WHERE reason = 'tag filter (unknown label)'"
            ) as cur:
                rows = await cur.fetchall()
        assert [row[0] for row in rows] == ["tag filter: unknown label(s) on Test Instance: nope"]

    @pytest.mark.asyncio()
    async def test_resolves_labels_to_ids(self, instance: Any, seeded_instances: None) -> None:
        """Known labels are mapped to their IDs on every enabled pass.

        Both sides of the lookup are normalised to lowercase before the
        dict.get fires: the *arr's labels are lowercased in
        :func:`ArrClient.get_tags`, and the operator's labels are
        lowercased in :func:`validate_tag_filter`.  The match itself
        is a plain case-sensitive ``dict.get`` against pre-lowered keys.
        """
        import dataclasses  # noqa: PLC0415

        scoped = dataclasses.replace(
            instance,
            cutoff=dataclasses.replace(instance.cutoff, cutoff_enabled=True),
            tag_filter=TagFilterPolicy(include=("1080p",), exclude=("uncut",)),
        )
        adapter = self._stub_adapter(
            get_tags_result={"1080p": 1, "4k": 2, "uncut": 7},
        )
        tag_ids = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert tag_ids == {
            SearchKind.missing: (frozenset({1}), frozenset({7})),
            SearchKind.cutoff: (frozenset({1}), frozenset({7})),
            SearchKind.upgrade: (None, None),
        }

    @pytest.mark.asyncio()
    async def test_unknown_labels_become_empty_set(
        self, instance: Any, seeded_instances: None
    ) -> None:
        """An include list of only-unknown labels resolves to an empty set.

        The downstream filter then skips every candidate (no item can
        match a label that does not exist on this *arr), which is the
        right safety default for an operator typo.
        """
        import dataclasses  # noqa: PLC0415

        scoped = dataclasses.replace(
            instance,
            tag_filter=TagFilterPolicy(include=("nope",), exclude=()),
        )
        adapter = self._stub_adapter(get_tags_result={"1080p": 1})
        tag_ids = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert tag_ids[SearchKind.missing] == (frozenset(), None)

    @pytest.mark.asyncio()
    async def test_get_tags_failure_disables_filter(
        self, instance: Any, seeded_instances: None
    ) -> None:
        """A transport error on /tag disables every pass's filter and logs once.

        The cycle must keep running; failing closed would block every
        search whenever an *arr is briefly unreachable.
        """
        import dataclasses  # noqa: PLC0415

        scoped = dataclasses.replace(
            instance,
            tag_filter=TagFilterPolicy(include=("1080p",), exclude=()),
        )
        adapter = self._stub_adapter(raises=ClientTransportError("network down"))
        tag_ids = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert tag_ids == self._ALL_OFF
